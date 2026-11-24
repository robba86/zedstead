"""SteamCMD operations — server update, mod download, mod ID resolution."""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.markup import escape

from app.modmgr.mods import breaks_a_line, is_workshop_id
from app.shared import steamcmd_lock
from app.shared.config import DayzConfig
from app.shared.platform import IS_WINDOWS, sudo_prefix

console = Console()

DAYZ_APP_ID = "221100"
DAYZ_SERVER_APP_ID = "223350"
# The Experimental server is its own app. 223350's `experimental_public` branch
# has been dead since 2019, so never `-beta` it (plans/66).
DAYZ_EXPERIMENTAL_SERVER_APP_ID = "1042420"
SERVER_APP_IDS = {"stable": DAYZ_SERVER_APP_ID, "experimental": DAYZ_EXPERIMENTAL_SERVER_APP_ID}
STEAMCMD_TAR_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
STEAMCMD_ZIP_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
STEAM_RATE_LIMIT_SECONDS = 5

#: curl for the SteamCMD tarball (plans/68 NET-12). ``-q`` has to be the first
#: argument, or curl reads ``~/.curlrc``. ``-f`` turns an HTTP error into a
#: non-zero exit with no body, so tar never reads an error page; ``-S`` keeps
#: curl's own message for the log. https only, redirects included, TLS 1.2 or
#: later, and a deadline so a stalled download cannot hang the install.
STEAMCMD_CURL_ARGV = [
    "curl", "-q", "-fsSL",
    "--proto", "=https", "--proto-redir", "=https", "--tlsv1.2",
    "--connect-timeout", "30", "--max-time", "600",
    STEAMCMD_TAR_URL,
]

#: Seconds the Windows SteamCMD download may wait on the connection or a read.
STEAMCMD_DOWNLOAD_TIMEOUT = 60


def install_steamcmd(
    install_path: Path, *, log: Callable[[str], None] | None = None
) -> bool:
    """Auto-install SteamCMD to the given directory. Returns True on success."""
    _emit = log or (lambda s: console.print(s))
    _emit("")

    if IS_WINDOWS:
        return _install_steamcmd_windows(install_path, log=_emit)
    return _install_steamcmd_linux(install_path, log=_emit)


def steamcmd_dependency_commands(missing: list[str]) -> list[list[str]]:
    """apt-get argv for installing ``missing``, or [] when nothing is missing.

    Pure — builds commands, runs nothing, so it can be asserted in tests. No
    test in this repo may execute apt.

    `apt-get`, not `apt`: apt prints "apt does not have a stable CLI interface.
    Use with caution in scripts." and means it. Matches install.sh and the
    Dockerfile.
    """
    if not missing:
        return []

    from app.shared.os_packages import needs_i386_architecture

    sudo = sudo_prefix()
    cmds: list[list[str]] = []
    # A multiarch 32-bit name needs the foreign architecture enabled first, or
    # apt-get reports "Unable to locate package". The trigger lives in
    # os_packages so no package-name spelling is duplicated in this file.
    if needs_i386_architecture(missing):
        cmds.append([*sudo, "dpkg", "--add-architecture", "i386"])
    # Without an update, a box whose package lists were pruned (any Docker base
    # image, some minimal cloud images) cannot resolve these names at all.
    cmds.append([*sudo, "apt-get", "update"])
    cmds.append(
        [*sudo, "apt-get", "install", "-y", "--no-install-recommends", *missing]
    )
    return cmds


def _install_steamcmd_linux(
    install_path: Path, *, log: Callable[[str], None] | None = None
) -> bool:
    """Install SteamCMD on Linux via apt + curl/tar."""
    _emit = log or (lambda s: console.print(s))
    from app.shared.os_packages import missing_packages, steamcmd_packages

    # Only touch apt when something is actually absent. This path needs sudo,
    # and on a box provisioned by `install.sh --yes` (or the Docker
    # image) everything here is already present — so the common case must not
    # prompt for a password to do nothing. missing_packages() needs no
    # privileges and returns [] on a non-dpkg host.
    missing = missing_packages(steamcmd_packages())
    if not missing:
        _emit("[dim]SteamCMD dependencies already present.[/dim]")
    else:
        _emit(f"[bold]Installing SteamCMD dependencies:[/bold] {' '.join(missing)}")
        # stdin=DEVNULL: apt-get must never block on a prompt when this runs
        # headless (web job / systemd). sudo still reads its password from
        # /dev/tty, so an interactive CLI run is unaffected.
        for cmd in steamcmd_dependency_commands(missing):
            try:
                proc = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except OSError as e:
                _emit(f"[yellow]Could not run {' '.join(cmd)} ({e}) — continuing.[/yellow]")
                break
            if proc.returncode != 0:
                # Non-fatal, deliberately: the steamcmd.sh existence check below
                # is the real gate, and failing here would delete a working
                # install for nothing (the host may not be Debian-family at
                # all). But say which packages are still missing — the old code
                # said only "dependency install failed", so a wrong package
                # name surfaced hours later as a loader error naming a .so.
                _emit(
                    f"[yellow]Dependency step failed (exit {proc.returncode}): "
                    f"{' '.join(cmd)}[/yellow]"
                )
                for line in (proc.stdout or "").strip().splitlines()[-5:]:
                    _emit(f"[dim]{line}[/dim]")
                still = missing_packages(steamcmd_packages())
                if still:
                    _emit(
                        "[yellow]Still missing: " + " ".join(still) +
                        " — install manually if SteamCMD fails to start.[/yellow]"
                    )
                break

    install_path.mkdir(parents=True, exist_ok=True)

    _emit(f"[bold]Downloading SteamCMD to {install_path}...[/bold]")
    try:
        curl = subprocess.Popen(
            list(STEAMCMD_CURL_ARGV),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tar = subprocess.run(
            ["tar", "zxf", "-", "-C", str(install_path)],
            stdin=curl.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if curl.stdout is not None:
            curl.stdout.close()
        curl_rc = curl.wait()
        curl_err = (curl.stderr.read().decode(errors="replace") if curl.stderr else "").strip()
        if curl.stderr is not None:
            curl.stderr.close()
        if curl_rc != 0:
            _emit(f"[red]Download failed (curl exit {curl_rc}): {curl_err}[/red]")
            return False
        if tar.returncode != 0:
            _emit(f"[red]Extract failed (tar exit {tar.returncode}): {(tar.stdout or '').strip()}[/red]")
            return False
    except OSError as e:
        _emit(f"[red]Failed to download/extract SteamCMD: {e}[/red]")
        return False

    steamcmd_sh = install_path / "steamcmd.sh"
    if steamcmd_sh.is_file():
        _emit(f"[green]✅ SteamCMD installed to {install_path}[/green]")
        return True
    else:
        _emit("[red]SteamCMD extraction succeeded but steamcmd.sh not found.[/red]")
        return False


def _install_steamcmd_windows(
    install_path: Path, *, log: Callable[[str], None] | None = None
) -> bool:
    """Install SteamCMD on Windows via urllib + zipfile (stdlib only)."""
    import io
    import urllib.request
    import zipfile

    _emit = log or (lambda s: console.print(s))
    install_path.mkdir(parents=True, exist_ok=True)

    _emit(f"[bold]Downloading SteamCMD to {install_path}...[/bold]")
    try:
        # A timeout per blocking read, so a stalled CDN fails instead of
        # hanging the install for good.
        with urllib.request.urlopen(
            STEAMCMD_ZIP_URL, timeout=STEAMCMD_DOWNLOAD_TIMEOUT
        ) as resp:
            data = resp.read()
    except Exception as e:
        _emit(f"[red]Failed to download SteamCMD: {e}[/red]")
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(install_path)
    except Exception as e:
        _emit(f"[red]Failed to extract SteamCMD: {e}[/red]")
        return False

    steamcmd_exe = install_path / "steamcmd.exe"
    if not steamcmd_exe.is_file():
        _emit("[red]SteamCMD extraction succeeded but steamcmd.exe not found.[/red]")
        return False

    _emit(f"[green]✅ SteamCMD installed to {install_path}[/green]")

    # First run: SteamCMD self-updates and Windows Firewall may prompt for
    # network access.  Run "+quit" so the user can approve the dialog before
    # we attempt a real login.
    _emit("")
    _emit("[bold yellow]Windows may ask you to allow SteamCMD through the firewall.[/bold yellow]")
    _emit("[dim]If a firewall dialog appears, click 'Allow' to continue.[/dim]")
    _emit("[bold]Running SteamCMD first-time setup...[/bold]")
    try:
        subprocess.run(
            [str(steamcmd_exe), "+quit"],
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        _emit("[yellow]SteamCMD first-run timed out — you may need to allow firewall access.[/yellow]")
    except OSError as e:
        _emit(f"[yellow]SteamCMD first-run failed: {e}[/yellow]")

    return True


@dataclass(frozen=True)
class LoginDiagnosis:
    """What a failed non-interactive SteamCMD login attempt actually means.

    ``factor`` names the second factor Steam is waiting for (``"email"`` or
    ``"mobile"``, ``None`` when the output doesn't say); ``error`` is a
    user-facing message for a failure that **no** code can fix — a wrong
    password, a rate limit, a rejected code. Both ``None`` means "unknown
    failure", and callers should fall back to asking for a code.
    """

    factor: str | None = None
    error: str | None = None


# An anonymous SteamCMD login works (`+login anonymous +quit` exits 0), but it
# owns nothing, and the DayZ server (223350) and DayZ's Workshop items can only
# be downloaded by an account that owns DayZ. SteamCMD's whole answer is one
# line, measured on an Ubuntu 24.04 VPS in 2026 Q3:
#   ERROR! Failed to install app '223350' (No subscription)
# followed by exit status 8, which alone explains nothing.
_OWNERSHIP_RULE = (
    "The DayZ server and its Workshop mods can only be downloaded by an account "
    "that owns DayZ; an anonymous login cannot."
)
_OWNER_LOGIN_HINT = (
    "Log in with the Steam account that owns DayZ: zsm steam-login --username <name>."
)

# What every surface that takes a Steam username says when it refuses one.
ANONYMOUS_USERNAME_ERROR = (
    "'anonymous' can't be used as the Steam username (SteamCMD: No subscription). "
    f"{_OWNERSHIP_RULE} Use the Steam account that owns DayZ."
)


def is_anonymous_username(username: str) -> bool:
    """True for SteamCMD's anonymous login, however it was typed."""
    return (username or "").strip().lower() == "anonymous"


def steamcmd_word_problem(label: str, value: str) -> str | None:
    """Why *value* cannot go to SteamCMD as one argument, or None.

    SteamCMD reads an argument that starts with ``+`` as its next command, and
    a space or line break inside one does not keep it one word. The rule is
    deliberately this narrow, not "letters, digits and _": no real account
    name or Guard code is refused by it. Markup-free, for every surface.
    """
    if any(ch.isspace() for ch in value):
        return f"{label} cannot contain spaces or line breaks."
    if value[:1] in ("+", "-"):
        return f"{label} cannot start with '{value[0]}'."
    return None


_USERNAME_LABEL = "The Steam username"


def steam_username_problem(username: str) -> str | None:
    """Why *username* cannot be the Steam login, or None. The one check every
    place that sets ``steam_username`` asks: anonymous, then the argument rule."""
    if is_anonymous_username(username):
        return ANONYMOUS_USERNAME_ERROR
    return steamcmd_word_problem(_USERNAME_LABEL, username)


class SteamcmdRefused(RuntimeError):
    """SteamCMD was not started, because something it would have been given
    is wrong. ``str()`` is the reason, markup-free, for the CLI and a job log."""


class SteamUsernameRefused(SteamcmdRefused):
    """The configured username is not one SteamCMD argument."""


def steamcmd_username(config: DayzConfig) -> str:
    """``config.steam_username`` for a SteamCMD command line, checked.

    Every builder that puts the configured username after ``+login`` takes it
    from here. The setters refuse a bad one, but a value can arrive without
    passing them (the Docker env var, a restored config.json, a hand edit), and
    SteamCMD would read one starting with ``+`` as its next command. Raises
    `SteamUsernameRefused` with the setters' own message.

    An empty username passes: every caller has its own refusal for that.
    So does ``anonymous``: SteamCMD's "No subscription" is explained where it
    happens (`no_subscription_error`).
    """
    problem = steamcmd_word_problem(_USERNAME_LABEL, config.steam_username)
    if problem:
        raise SteamUsernameRefused(problem)
    return config.steam_username


# A password or a Steam Guard code on SteamCMD's command line can be read by
# every local user in /proc/<pid>/cmdline for as long as SteamCMD runs (up to
# two minutes at a Guard prompt). The web login therefore writes them into a
# script SteamCMD runs with `+runscript`, in a directory only this user can
# open, and removes it when SteamCMD is done. Each value is written inside
# double quotes, so a space, `;` or `//` in a password stays part of it; what
# cannot be written inside quotes on one line is refused before SteamCMD runs.
_SCRIPT_UNWRITABLE = ('"', "\r", "\n", "\0")


class LoginScriptRefused(SteamcmdRefused):
    """A login value cannot be written into a SteamCMD script."""


def login_script_problem(label: str, value: str) -> str | None:
    """Why *value* cannot go into a SteamCMD login script, or None.
    Markup-free, and it never quotes the value: it may be a password."""
    if any(ch in value for ch in _SCRIPT_UNWRITABLE) or breaks_a_line(value):
        return (
            f"{label} contains a double quote, a line break or a control "
            "character, which Zedstead cannot pass to SteamCMD. Log in on the "
            "server with 'zsm steam-login' instead, where SteamCMD asks for "
            "it itself."
        )
    return None


def login_script_problems(username: str, password: str, guard_code: str = "") -> str | None:
    """The first of the three values that cannot go into a login script."""
    for label, value in (
        (_USERNAME_LABEL, username),
        ("The Steam password", password),
        ("The Steam Guard code", guard_code),
    ):
        problem = login_script_problem(label, value)
        if problem:
            return problem
    return None


@contextmanager
def login_script(username: str, password: str, guard_code: str = "") -> Iterator[Path]:
    """A SteamCMD script that logs in and quits, readable by this user only.

    The directory is ``mkdtemp``'s (0700) and the file is created 0600, so
    nobody else can open it at any point. It is removed when the ``with``
    block ends, however it ends: a timeout, a cancelled job and a killed one
    all leave through here.

    ``@ShutdownOnFailedCommand 1`` makes a failed login end SteamCMD with an
    error, as ``+login user pass +quit`` does; without it the script would go
    on to ``quit`` and could exit 0 after a refused password. Nothing else
    changes: SteamCMD still prompts for a Guard code it was not given, which
    is what ``diagnose_login_output`` reads.
    """
    problem = login_script_problems(username, password, guard_code)
    if problem:
        raise LoginScriptRefused(problem)
    values = [username, password] + ([guard_code] if guard_code else [])
    text = (
        "@ShutdownOnFailedCommand 1\n"
        + "login " + " ".join(f'"{v}"' for v in values) + "\n"
        + "quit\n"
    )
    directory = Path(tempfile.mkdtemp(prefix="zsm-steam-login-"))
    try:
        path = directory / "login.txt"
        # O_BINARY: on Windows a text-mode descriptor would write \r\n.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags, 0o600), "wb") as fh:
            fh.write(text.encode("utf-8"))
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def login_script_cmd(config: DayzConfig, script: Path) -> list[str]:
    """SteamCMD running *script*. The trailing ``+quit`` only matters if the
    script could not be read, when SteamCMD would otherwise wait at its own
    prompt."""
    return [str(config.steamcmd_path), "+runscript", str(script), "+quit"]


def no_subscription_error(output: str, username: str = "") -> str | None:
    """The explanation for SteamCMD's ``No subscription``, or None without one.

    Markup-free, so the CLI, a job log and an HTTP error can all carry it.
    *username* is the account the command logged in as; SteamCMD's own
    ``Connecting anonymously`` line is believed as well, for a caller that
    cannot say.
    """
    text = (output or "").lower()
    if "no subscription" not in text:
        return None
    name = (username or "").strip()
    if is_anonymous_username(name) or "connecting anonymously" in text:
        who = "SteamCMD is logged in as 'anonymous', which does not own DayZ"
    elif name:
        who = f"The Steam account '{name}' does not own DayZ"
    else:
        who = "This Steam account does not own DayZ"
    return f"{who} (SteamCMD: No subscription). {_OWNERSHIP_RULE} {_OWNER_LOGIN_HINT}"


def runner_no_subscription_error(runner, config: DayzConfig) -> str | None:
    """:func:`no_subscription_error` for the command *runner* ran last.

    Runners keep that command's output in ``last_output`` (the web's
    JobRunner, the CLI's pipeline runner); one that keeps none reads as "".
    """
    return no_subscription_error(getattr(runner, "last_output", ""), config.steam_username)


class NoSubscriptionError(subprocess.CalledProcessError):
    """SteamCMD refused the download: the account does not own DayZ.

    Still a ``CalledProcessError``, so every existing handler catches it; its
    ``str()`` is the explanation instead of the argv and the exit status.
    """

    def __init__(self, returncode: int, cmd, message: str, output=None) -> None:
        super().__init__(returncode, cmd, output)
        self.message = message

    def __str__(self) -> str:
        return self.message


def diagnose_login_output(output: str) -> LoginDiagnosis:
    """Classify SteamCMD's combined stdout/stderr from a failed login.

    A non-zero SteamCMD exit alone says nothing: a Guard-protected account, a
    typo'd password and a rate-limited account all exit non-zero. Treating
    every failure as "a code was emailed" is what made a wrong password look
    like a missing email, so callers pass the output through here.

    Order matters — the rejected-code and rate-limit strings also contain the
    generic "two factor" / "Steam Guard" words, and the two-factor (mobile
    authenticator) prompt mentions Steam Guard too, so the mobile markers are
    tested before the email ones.
    """
    not_owned = no_subscription_error(output)
    if not_owned:
        return LoginDiagnosis(error=not_owned)

    text = output.lower()

    if (
        "code mismatch" in text
        or "two factor code mismatch" in text
        or "invalid code" in text
    ):
        return LoginDiagnosis(
            error="Steam rejected that Steam Guard code. Request a new code and "
            "try again — codes expire and are single-use."
        )
    if "expired code" in text:
        return LoginDiagnosis(
            error="That Steam Guard code has expired. Request a new one."
        )
    if "rate limit" in text or "throttle" in text:
        return LoginDiagnosis(
            error="Steam is rate-limiting logins for this account after too many "
            "attempts. Steam sends no new Guard code while throttled — wait "
            "about 30 minutes before trying again."
        )
    if "invalid password" in text or "invalid credentials" in text:
        return LoginDiagnosis(
            error="Steam rejected the username or password, so no Steam Guard "
            "code was sent. Check the credentials and try again."
        )
    if "account disabled" in text or "account locked" in text:
        return LoginDiagnosis(
            error="Steam reports this account as disabled or locked. Sign in on "
            "the Steam website to resolve it."
        )
    if (
        "two-factor" in text
        or "two factor" in text
        or "mobile authenticator" in text
        or "steam app" in text
    ):
        return LoginDiagnosis(factor="mobile")
    if (
        "logon denied" in text
        or "steam guard code" in text
        or "check your email" in text
    ):
        return LoginDiagnosis(factor="email")
    return LoginDiagnosis()


def check_steam_login(config: DayzConfig, timeout: int = 90, *, wait: float | None = None) -> bool:
    """Check if Steam credentials are cached (non-interactive login test).

    The timeout is generous on purpose: a hit kills SteamCMD mid-run, and a
    SteamCMD killed while it rewrites ``Steam/config/config.vdf`` loses the
    cached login token — the check would then *cause* the failure it reports.
    A cold run (self-update + "Verifying installation") takes 10-15s on a
    container, more under load.

    While another process runs SteamCMD this waits for it (`steamcmd_lock`);
    with *wait* seconds it raises `steamcmd_lock.SteamcmdBusy` after that
    long instead — ``wait=0`` for a caller that has an answer to fall back on.
    """
    if not config.steam_username or not config.steamcmd_path.is_file():
        return False
    try:
        cmd = [str(config.steamcmd_path), "+login", steamcmd_username(config), "+quit"]
    except SteamUsernameRefused:
        return False  # as for no username: `zsm steam-login` says why
    with steamcmd_lock.hold(cmd, wait=wait, on_wait=_waiting_notice):
        try:
            return steamcmd_lock.run_to_end(cmd, timeout=timeout).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False


def _waiting_notice(message: str) -> None:
    """How a CLI run says it is queued behind another process's SteamCMD."""
    console.print(f"[yellow]{escape(message)}[/yellow]")


def probe_login_failure(config: DayzConfig, timeout: int = 90) -> LoginDiagnosis:
    """Ask SteamCMD, non-interactively, what is wrong with this account.

    The interactive login hands the terminal straight to SteamCMD — that is
    what lets the user type the Guard code — so its output never passes
    through us and cannot be classified. This runs **a second, separate**
    codeless attempt with stdin closed and the output captured, and hands that
    to :func:`diagnose_login_output`.

    So it describes the account's state *now* (rate-limited, disabled, no
    cached token, which factor Steam wants), not necessarily the exact reason
    the attempt a moment ago failed. Say that when you print it. The one that
    earns its keep is the rate limit: after a few wrong codes Steam stops
    sending new ones, and without this the user retypes codes for half an hour
    against a server that is refusing all of them.

    Sequential by construction — the interactive SteamCMD has already exited —
    so this never puts two of them on one STEAMROOT.
    """
    if not config.steam_username or not config.steamcmd_path.is_file():
        return LoginDiagnosis()
    try:
        cmd = [str(config.steamcmd_path), "+login", steamcmd_username(config), "+quit"]
    except SteamUsernameRefused:
        return LoginDiagnosis()
    try:
        with steamcmd_lock.hold(cmd, on_wait=_waiting_notice):
            result = steamcmd_lock.run_to_end(cmd, timeout=timeout, capture=True)
    except (subprocess.TimeoutExpired, OSError):
        return LoginDiagnosis()

    def _text(part) -> str:
        if isinstance(part, bytes):
            return part.decode(errors="replace")
        return str(part or "")

    return diagnose_login_output(f"{_text(result.stdout)}\n{_text(result.stderr)}")


# How long the interactive login may sit before zsm gives the terminal back.
# Generous because a human is reading an email or unlocking a phone, and
# because the process being killed here is the one *creating* the cached
# token: SteamCMD killed mid-rewrite of Steam/config/config.vdf loses it (the
# same reason check_steam_login's timeout is 90s and not 15). After a quarter
# of an hour with no keystroke it is sitting at a prompt, not writing.
LOGIN_TIMEOUT = 900


class SteamLoginTimeout(RuntimeError):
    """The interactive login was abandoned at a prompt."""


# What both interactive entry points say before handing the terminal over.
# One copy: `zsm steam-login` and `zsm init` are the same first run over SSH,
# which is the situation this text exists for. The web says the same things in
# SteamLoginCard.jsx, worded for a form instead of a terminal.
#
# The mobile line is the one that matters. An account on the Steam mobile
# authenticator gets **no email at all**, and people sit waiting for one — it
# is the same distinction `diagnose_login_output`'s `factor` carries, said
# before the failure instead of after it.
LOGIN_PREAMBLE_LINES = (
    "SteamCMD now takes over this terminal and asks you, in order, for:",
    "  1. the password for this Steam account — Zedstead never sees or stores it",
    "  2. a Steam Guard code, if the account has Steam Guard turned on",
    "The code comes from the Steam mobile app if you use the authenticator, and",
    "in that case Steam sends no email at all. Otherwise it is in the email",
    "Steam sends the moment the password is accepted. Codes are single-use and",
    "expire, so use the newest one.",
    "Only the username is written to the Zedstead config; the login itself is",
    "cached by SteamCMD.",
)


def print_login_preamble(emit: Callable[[str], None] | None = None) -> None:
    """Print LOGIN_PREAMBLE_LINES. Markup-free, so a job log can take it too."""
    _emit = emit or (lambda s: console.print(s, markup=False))
    for line in LOGIN_PREAMBLE_LINES:
        _emit(line)


def _run_interactive_login(cmd: list[str], timeout: int | None) -> None:
    """Run SteamCMD with the terminal attached, bounded by *timeout*.

    ``subprocess.run(timeout=...)`` SIGKILLs on expiry; this asks first and
    only escalates, because the thing being stopped may be halfway through
    writing the cached login token. Raises ``CalledProcessError`` on a non-zero
    exit, exactly as before, and ``SteamLoginTimeout`` when nobody answered.
    """
    with steamcmd_lock.hold(cmd, on_wait=_waiting_notice):
        _run_attached(cmd, timeout)


def _run_attached(cmd: list[str], timeout: int | None) -> None:
    proc = subprocess.Popen(cmd)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise SteamLoginTimeout(
            f"No answer for {timeout}s — SteamCMD was left waiting at a prompt "
            "and has been stopped. Run 'zsm steam-login' again when you have "
            "the code to hand."
        ) from None
    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)


def steam_login(config: DayzConfig, *, timeout: int | None = LOGIN_TIMEOUT) -> None:
    """Log in to Steam to refresh cached credentials.

    Says when it failed and nothing when it worked: the caller reports the
    success, once (`zsm steam-login` printed it twice while both did).

    Prints what SteamCMD is about to ask for *before* handing over the
    terminal — over SSH the Guard prompt is the commonest place a first run
    stalls, and an unexplained `Steam Guard code:` on a machine with no mail
    client reads as a dead end.

    On Windows the first invocation may fail while the user approves a
    Windows Firewall dialog.  We retry once after a short pause so the
    login can succeed without the user having to re-run the whole wizard.
    """
    cmd = [
        str(config.steamcmd_path),
        "+login", steamcmd_username(config),
        "+quit",
    ]

    console.print("[bold]Logging in to Steam...[/bold]")
    print_login_preamble(lambda s: console.print(f"[dim]{escape(s)}[/dim]"))
    if IS_WINDOWS:
        console.print(
            "[dim]If a Windows Firewall dialog appears, click 'Allow'.[/dim]"
        )
    try:
        _run_interactive_login(cmd, timeout)
        return
    except subprocess.CalledProcessError:
        if not IS_WINDOWS:
            console.print("[red]Steam login failed.[/red]")
            raise

    # Windows: a first attempt can fail while a firewall prompt is open, so it
    # is tried once more. SteamCMD owns the terminal, so zsm cannot read why
    # it failed and must not name a cause: this line used to blame the
    # firewall right under SteamCMD's own "ERROR (Invalid Password)".
    console.print("[yellow]That login did not work. SteamCMD's reason is in the lines above.[/yellow]")
    console.print(
        "[bold]Trying once more in 10 seconds.[/bold] "
        "[dim]A first login on Windows can fail while a Windows Firewall prompt is open.[/dim]"
    )
    time.sleep(10)
    try:
        _run_interactive_login(cmd, timeout)
    except subprocess.CalledProcessError:
        console.print("[red]Steam login failed.[/red]")
        raise


# How much of a command's output run_passthrough keeps. SteamCMD states why it
# failed on its last lines, so the tail is all a diagnosis needs.
_OUTPUT_TAIL_BYTES = 64 * 1024
# How long to wait for the output after the process has exited. Only a
# descendant still holding the terminal (an orphaned SteamCMD, whose wrapper
# was killed) keeps it open longer, and then its output is not ours to wait for.
_READER_GRACE_SECONDS = 5
# After this long without output, run_passthrough says that SteamCMD is still
# working. On Windows there is no pseudo-terminal, so SteamCMD writes to a pipe,
# block-buffers it and prints nothing for the whole server download: the last
# line on screen is its banner "-- type 'quit' to exit --", which reads as a
# prompt and as a freeze (role 1 user test, 2026 Q4).
_QUIET_SECONDS = 30.0
_QUIET_POLL_SECONDS = 1.0


def _quiet_notice(elapsed: float) -> str:
    minutes, seconds = divmod(int(elapsed), 60)
    so_far = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
    return (
        f"Zedstead: SteamCMD is still working ({so_far} so far). It can print nothing "
        "for minutes while it downloads. \"-- type 'quit' to exit --\" is its "
        "banner, not a question: there is nothing to type."
    )


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _write_through(stream, chunk: bytes) -> None:
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            stream.flush()
            buffer.write(chunk)
            buffer.flush()
        else:
            stream.write(chunk.decode(errors="replace"))
            stream.flush()
    except (OSError, ValueError):
        pass  # a closed or broken stdout must not take the install down with it


def run_passthrough(cmd: list[str], *, timeout: float | None = None) -> tuple[int, str]:
    """Run *cmd* with its output on this terminal; return ``(returncode, tail)``.

    SteamCMD used to inherit the terminal, so its output never passed through
    zsm and a failed install could only be reported as an exit status (8, for
    ``No subscription``). This copies every byte through as it arrives and
    keeps the last :data:`_OUTPUT_TAIL_BYTES` for :func:`no_subscription_error`.

    On a terminal the child writes to a pseudo-terminal, not a pipe: SteamCMD
    block-buffers a pipe, and a server install would then sit silent for
    minutes. Off a terminal (the journal, docker logs) it gets a pipe, which is
    what it inherited before. stdin stays closed, as it always was.

    Raises ``subprocess.TimeoutExpired`` (with the output so far) after ending
    the process group (`steamcmd_lock.end_group`), so the real SteamCMD behind
    the steamcmd.sh wrapper is gone before the lock is free.

    A SteamCMD *cmd* first waits for any other process's SteamCMD to finish
    (`steamcmd_lock`); the timeout counts from when it starts.
    """
    if steamcmd_lock.is_steamcmd(cmd):
        with steamcmd_lock.hold(cmd, on_wait=_waiting_notice):
            return _passthrough(cmd, timeout)
    return _passthrough(cmd, timeout)


def _passthrough(cmd: list[str], timeout: float | None) -> tuple[int, str]:
    stream = sys.stdout
    if not IS_WINDOWS and _isatty(stream):
        import pty

        read_fd, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                start_new_session=True,
            )
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(slave)
        close = functools.partial(os.close, read_fd)
    else:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=not IS_WINDOWS,
        )
        read_fd = proc.stdout.fileno()
        close = proc.stdout.close

    tail = bytearray()
    # When output last arrived, and whether the screen ends mid-line.
    seen = {"at": time.monotonic(), "open_line": False}

    def _pump() -> None:
        try:
            while True:
                try:
                    chunk = os.read(read_fd, 65536)
                except OSError:  # EIO: every writer of the pseudo-terminal is gone
                    break
                if not chunk:
                    break
                _write_through(stream, chunk)
                seen["at"] = time.monotonic()
                seen["open_line"] = not chunk.endswith(b"\n")
                tail.extend(chunk)
                if len(tail) > _OUTPUT_TAIL_BYTES:
                    del tail[:-_OUTPUT_TAIL_BYTES]
        finally:
            close()

    reader = threading.Thread(target=_pump, name="steamcmd-output", daemon=True)
    reader.start()
    started = time.monotonic()
    deadline = None if timeout is None else started + timeout
    try:
        while True:
            wait = _QUIET_POLL_SECONDS
            if deadline is not None:
                wait = max(0.0, min(wait, deadline - time.monotonic()))
            try:
                returncode = proc.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise
                if now - seen["at"] >= _QUIET_SECONDS:
                    lead = "\n" if seen["open_line"] else ""
                    _write_through(stream, (lead + _quiet_notice(now - started) + "\n").encode())
                    seen["at"] = now
                    seen["open_line"] = False
    except subprocess.TimeoutExpired:
        steamcmd_lock.end_group(proc, IS_WINDOWS)
        proc.wait()
        reader.join(_READER_GRACE_SECONDS)
        raise subprocess.TimeoutExpired(
            cmd, timeout, output=bytes(tail).decode(errors="replace")
        ) from None
    except BaseException:  # Ctrl-C: what subprocess.run does too
        steamcmd_lock.end_group(proc, IS_WINDOWS)
        proc.wait()
        raise
    reader.join(_READER_GRACE_SECONDS)
    return returncode, bytes(tail).decode(errors="replace")


def update_dayz_server(
    config: DayzConfig, *, log: Callable[[str], None] | None = None
) -> None:
    """Update the DayZ dedicated server via SteamCMD.

    Raises ``CalledProcessError`` on a failed run — a ``NoSubscriptionError``
    when SteamCMD's output says the account does not own DayZ.
    """
    from app.ops import mission_stock

    _emit = log or (lambda s: console.print(s))
    _emit("")
    _emit("[bold]Updating DayZ server...[/bold]")
    cmd = build_server_update_cmd(config)
    # validate puts back every stock mission file; this keeps the admin's (plans/67).
    stash = mission_stock.before_download(config, log=_emit)
    returncode, output = run_passthrough(cmd)
    if returncode != 0:
        mission_stock.download_failed(config, stash, log=_emit)
        reason = no_subscription_error(output, config.steam_username)
        error = (
            NoSubscriptionError(returncode, cmd, reason, output)
            if reason
            else subprocess.CalledProcessError(returncode, cmd, output)
        )
        _emit(f"[red]Error updating DayZ server: {error}[/red]")
        raise error
    mission_stock.after_download(config, stash, log=_emit)
    _emit("[green]DayZ server updated successfully.[/green]")
    _emit("")


def download_mod(
    config: DayzConfig,
    mod_id: str,
    mod_name: str = "",
    max_retries: int = 5,
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """Download a single workshop mod via SteamCMD with validate and retry.

    Raises RuntimeError when SteamCMD keeps exiting 0 without leaving anything
    behind — a silently-failed download must not read as a successful one —
    and ``NoSubscriptionError``, without retrying, when the account does not
    own DayZ.
    """
    _emit = log or (lambda s: console.print(s))
    display = mod_name or mod_id
    if not config.steam_username:
        raise RuntimeError(f"Cannot download {display}. {STEAM_LOGIN_HINT}")

    try:
        cmd = build_mod_download_cmd(config, mod_id)
    except SteamcmdRefused as e:
        _emit(f"[red]{display}: {e}[/red]")
        raise

    def _stop_if_not_owned(returncode: int, output) -> None:
        reason = no_subscription_error(output or "", config.steam_username)
        if reason:
            _emit(f"[red]{display}: {reason}[/red]")
            raise NoSubscriptionError(returncode, cmd, reason, output)

    repaired = False
    for attempt in range(1, max_retries + 1):
        try:
            returncode, output = run_passthrough(cmd, timeout=600)
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, cmd, output)
            if not is_mod_downloaded(config, mod_id):
                _stop_if_not_owned(returncode, output)
                if not repaired:
                    repaired = True
                    repair_workshop_manifest_before_retry(config, _emit)
                _emit(f"[yellow]{display}: {EMPTY_DOWNLOAD_HINT}[/yellow]")
                if attempt < max_retries:
                    time.sleep(STEAM_RATE_LIMIT_SECONDS)
                    continue
                raise RuntimeError(f"{display}: {EMPTY_DOWNLOAD_HINT}")
            _emit(f"[green]{display} downloaded and validated.[/green]")
            return
        except subprocess.TimeoutExpired:
            _emit(
                f"[red]{display} download timed out (attempt {attempt}/{max_retries}).[/red]"
            )
            if attempt >= max_retries:
                raise
        except NoSubscriptionError:
            raise  # retrying cannot help: the account is the problem, not the download
        except subprocess.CalledProcessError as e:
            _stop_if_not_owned(e.returncode, e.output)
            if attempt < max_retries:
                _emit(
                    f"[yellow]Download attempt {attempt}/{max_retries} failed for "
                    f"{display} (exit {e.returncode}). Retrying in {STEAM_RATE_LIMIT_SECONDS}s...[/yellow]"
                    f"[dim]This happens if downloading large file (eg. @DeerIsle is ~11GB).[/dim]"
                    f"[dim]Just wait for the attempts to finish, if not just rerun...[/dim]"
                )
                time.sleep(STEAM_RATE_LIMIT_SECONDS)
            else:
                _emit(
                    f"[red]Failed to download {display} after {max_retries} "
                    f"attempts: {e}[/red]"
                )
                raise


STEAM_LOGIN_HINT = (
    "No Steam username is configured — workshop downloads need a logged-in account. "
    "Run `zsm steam-login` (Docker: `docker compose exec -u zsm dayz zsm steam-login`)."
)

EMPTY_DOWNLOAD_HINT = (
    "SteamCMD exited 0 but left no files in the workshop folder. Usual causes: an "
    "expired Steam login (run `zsm steam-login`), or a stale workshop manifest — "
    "SteamCMD aborts with 'Missing game files' when appworkshop_221100.acf still "
    "lists an item whose folder was deleted. Run `zsm cleanup repair-workshop` to "
    "fix the manifest, then retry."
)


def repair_workshop_manifest_before_retry(
    config: DayzConfig, emit: Callable[[str], None]
) -> bool:
    """Drop stale ACF entries between download attempts. True when it changed something.

    A workshop item whose folder was deleted but that is still listed in
    ``appworkshop_221100.acf`` makes SteamCMD abort *any* download for the app
    with ``Missing game files`` while still exiting 0. Repairing it turns an
    infinite "retry, exit 0, no files" loop into a working download.
    """
    from app.ops.cleanup_ops import repair_workshop_manifest

    try:
        removed = repair_workshop_manifest(config)
    except Exception:
        return False
    if removed:
        emit(
            f"[yellow]Repaired SteamCMD's workshop manifest — dropped {len(removed)} "
            "stale entry(s) that were aborting the download.[/yellow]"
        )
    return bool(removed)


def is_mod_downloaded(config: DayzConfig, mod_id: str) -> bool:
    """True when the workshop item is on disk *with real files*.

    SteamCMD leaves a hollow item directory behind when a download silently
    fails, so an existence check alone reports a broken download as a good one
    — and callers then skip re-downloading it or copy the empty folder into the
    server dir, where the mod looks installed but ships no PBOs, keys or XML.

    A failed download typically leaves the *directory skeleton*
    (``Addons/``, ``Keys/``) with nothing in it, so "contains any entry" is not
    enough either: this requires at least one actual file somewhere in the tree.
    """
    item_dir = config.workshop_dir / mod_id
    if not item_dir.is_dir():
        return False
    try:
        return any(p.is_file() for p in item_dir.rglob("*"))
    except OSError:
        return False


# ── Command builders (DRY helpers) ──────────────────────────────────


class WorkshopIdRefused(SteamcmdRefused):
    """A Workshop ID that is not ASCII digits (plans/68 CON-12)."""


def build_mod_download_cmd(config: DayzConfig, mod_id: str) -> list[str]:
    """Build SteamCMD command to download a single workshop mod.

    Raises `WorkshopIdRefused` for an ID that is not ASCII digits: it would be
    one SteamCMD argument and a folder under the workshop directory.
    """
    if not is_workshop_id(mod_id):
        raise WorkshopIdRefused("A Workshop ID is digits only, and this one is not.")
    return [
        str(config.steamcmd_path),
        "+login", steamcmd_username(config),
        "+workshop_download_item", DAYZ_APP_ID, mod_id, "validate",
        "+quit",
    ]


def server_branch(config: DayzConfig) -> str:
    """The instance's branch, "stable" or "experimental". The only reader of
    `config.server_branch`: a hand-edited value it does not know reads as stable."""
    branch = (config.server_branch or "").strip().lower()
    return branch if branch in SERVER_APP_IDS else "stable"


def server_app_id(config: DayzConfig) -> str:
    """The Steam app the server installs from. Never dial a literal instead."""
    return SERVER_APP_IDS[server_branch(config)]


BRANCH_LABELS = {"stable": "stable", "experimental": "Experimental"}


class ServerBranchMismatch(SteamcmdRefused):
    """The server directory holds the other branch's server (plans/66 D1)."""


def installed_server_branch(server_dir: Path) -> str | None:
    """Which branch SteamCMD installed into *server_dir*, read from the
    `steamapps/appmanifest_<app id>.acf` it leaves there: "stable",
    "experimental", "mixed" when both apps were installed into the one directory
    (its files are then whichever ran last), or None when neither is."""
    steamapps = Path(server_dir) / "steamapps"
    found = [b for b, app in SERVER_APP_IDS.items() if (steamapps / f"appmanifest_{app}.acf").is_file()]
    if len(found) > 1:
        return "mixed"
    return found[0] if found else None


def branch_refusal(config: DayzConfig, wanted: str) -> str | None:
    """Why *config*'s server directory cannot become the *wanted* branch's
    server, or None. Markup-free: it reaches a job log, a 409, a Doctor row and
    the CLI. There is no in-place switch (plans/66 D1); the way out is an
    instance of its own."""
    installed = installed_server_branch(config.server_dir_path)
    if installed is None or installed == wanted:
        return None
    server_dir = config.server_dir_path
    if installed == "mixed":
        return (f"{server_dir} holds both the stable and the Experimental DayZ server: both were "
                "installed into it, and its files are whichever ran last. Reinstall the server to "
                "start clean (zsm cleanup reinstall-server).")
    have, want = BRANCH_LABELS[installed], BRANCH_LABELS[wanted]
    return (f"{server_dir} holds the {have} DayZ server, and one directory cannot hold both. Put the "
            f"{want} server in an instance of its own: zsm instances clone "
            f"{config.instance_id or 'main'} <name> --server-dir <new dir> --branch {wanted}")


def branch_mismatch(config: DayzConfig) -> str | None:
    """Why the server directory must not be updated as this instance's branch,
    or None."""
    wanted = server_branch(config)
    refusal = branch_refusal(config, wanted)
    installed = installed_server_branch(config.server_dir_path)
    if refusal is None or installed == "mixed":
        return refusal
    # The command goes last, so nothing trails it when it is copied.
    return (f"This instance is set to {BRANCH_LABELS[wanted]} (set server_branch back to "
            f"{installed} to keep updating the server it has). {refusal}")


def build_server_update_cmd(config: DayzConfig) -> list[str]:
    """Build SteamCMD command to update the DayZ dedicated server.

    Every server download goes through here, so this is also where one that
    would overwrite the other branch's server is refused (`ServerBranchMismatch`).
    """
    mismatch = branch_mismatch(config)
    if mismatch:
        raise ServerBranchMismatch(mismatch)
    return [
        str(config.steamcmd_path),
        "+force_install_dir", str(config.server_dir_path),
        "+login", steamcmd_username(config),
        "+app_update", server_app_id(config), "validate",
        "+quit",
    ]


async def download_mod_async(runner, config: DayzConfig, mod_id: str, mod_name: str = "", max_retries: int = 5) -> bool:
    """Download a mod via a runner (CLI or web job) with retry. Returns True on success.

    Success means the item actually landed, not merely that SteamCMD exited 0 —
    it does that on silently-failed workshop downloads too.
    """
    import asyncio

    display = mod_name or mod_id
    if not config.steam_username:
        runner.write(f"[red]Cannot download {display}. {STEAM_LOGIN_HINT}[/red]")
        return False

    try:
        cmd = build_mod_download_cmd(config, mod_id)
    except SteamcmdRefused as e:
        runner.write(f"[red]  {display}: {e}[/red]")
        return False
    repaired = False
    for attempt in range(1, max_retries + 1):
        # A web JobRunner exposes `cancelled`. Without this a cancelled job
        # still spawned SteamCMD max_retries times for every remaining mod.
        if getattr(runner, "cancelled", False):
            return False
        returncode = await runner.run_command(cmd)
        if returncode == 0 and is_mod_downloaded(config, mod_id):
            return True
        not_owned = runner_no_subscription_error(runner, config)
        if not_owned:
            # Retrying cannot help: the account is the problem, not the download.
            runner.write(f"[red]  {display}: {not_owned}[/red]")
            return False
        if returncode == 0:
            if not repaired:
                repaired = True
                # Fast (parse ACF + stat dirs) — kept on the loop so the
                # runner's write stays on its own thread/loop.
                repair_workshop_manifest_before_retry(config, runner.write)
            runner.write(f"[yellow]  {display}: {EMPTY_DOWNLOAD_HINT}[/yellow]")
        if attempt < max_retries and not getattr(runner, "cancelled", False):
            runner.write(
                f"[yellow]  Attempt {attempt}/{max_retries} failed for {display}. "
                f"Retrying in {STEAM_RATE_LIMIT_SECONDS}s...[/yellow]"
                f"[dim]This happens if downloading large file (eg. @DeerIsle is ~11GB).[/dim]"
                f"[dim]Just wait for the attempts to finish, if not just rerun...[/dim]"
            )
            await asyncio.sleep(STEAM_RATE_LIMIT_SECONDS)
    return False


WORKSHOP_BROWSE_URL = "https://steamcommunity.com/workshop/browse/"


def workshop_browse_url(query: str) -> str:
    """The Workshop browse URL that searches DayZ items for *query*.

    The query is percent-encoded, so every character of it reaches Steam as
    part of ``searchtext``: ``&``, ``#``, ``+`` and spaces included.
    """
    return WORKSHOP_BROWSE_URL + "?" + urllib.parse.urlencode(
        {"appid": DAYZ_APP_ID, "searchtext": query}, quote_via=urllib.parse.quote
    )


def search_workshop(query: str) -> list[tuple[str, str]]:
    """Search Steam Workshop for DayZ mods.

    Returns list of (workshop_id, title) tuples for all results found.
    """
    time.sleep(STEAM_RATE_LIMIT_SECONDS)

    search_url = workshop_browse_url(query)

    try:
        response = requests.get(search_url, timeout=30)
    except (OSError, requests.RequestException):
        return []
    if response.status_code != 200:
        return []
    return _parse_workshop_results(response.content)


def _result_title(link, mod_id: str) -> str:
    """The item's title for a browse-page result link, or ``Unknown (<id>)``."""
    card = link.find_parent("div", class_="workshopItem")
    if card is not None:
        title_elem = card.find("div", class_="workshopItemTitle")
        if title_elem is not None and title_elem.get_text(strip=True):
            return title_elem.get_text(strip=True)
    img = link.find("img", alt=True)
    if img is not None and img["alt"].strip():
        return img["alt"].strip()
    return link.get_text(strip=True) or f"Unknown ({mod_id})"


def _parse_workshop_results(content: bytes) -> list[tuple[str, str]]:
    """``(workshop_id, title)`` for every result on a Workshop browse page, in page order.

    Steam has rewritten this page: the ``workshopItem`` / ``workshopItemTitle``
    classes it used to ship are gone, replaced by hashed ones, so every title
    read as ``Unknown (<id>)`` — and a caller that matches on titles then finds
    nothing at all. The title now comes off the result thumbnail's ``alt``,
    which carries the item's name, with the old classes kept as first choice for
    any page still serving them. A title that cannot be read is still reported
    as ``Unknown (<id>)``, which no caller may treat as a name.
    """
    soup = BeautifulSoup(content, "html.parser")
    results: list[tuple[str, str]] = []
    seen_ids: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "filedetails" not in href or "?id=" not in href:
            continue
        mod_id = href.split("?id=")[1].split("&")[0]
        if not mod_id.isdigit() or mod_id in seen_ids:
            continue
        seen_ids.add(mod_id)
        results.append((mod_id, _result_title(link, mod_id)))

    return results


def _title_key(name: str) -> str:
    """``@Dabs Framework``, ``Dabs-Framework`` and ``dabs framework`` share one key."""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def resolve_mod_id(
    mod_name: str,
    cached_names: set[str],
) -> str | None:
    """Look up a mod's Workshop ID by Steam search. Returns None if cached or not found.

    Only a result *titled* as the mod counts, compared without case, ``@`` or
    separators. Steam's search always lists something — for a name nothing
    matches, unrelated items — so taking the first link installed whatever Steam
    put there under the typed name (a garbled ``@DayZ-Expansion-AI`` entry became
    "DayZ Earthquake") and cached that pairing for every later run.
    """
    if mod_name in cached_names:
        console.print(f"[dim]{mod_name} already cached.[/dim]")
        return None

    time.sleep(STEAM_RATE_LIMIT_SECONDS)

    search_url = workshop_browse_url(mod_name)
    console.print(f"[dim]Searching: {search_url}[/dim]")

    response = requests.get(search_url, timeout=30)
    if response.status_code != 200:
        console.print(f"[red]Could not retrieve page for {mod_name}[/red]")
        return None

    results = _parse_workshop_results(response.content)
    wanted = _title_key(mod_name)
    for mod_id, title in results:
        if wanted and _title_key(title) == wanted:
            console.print(f"[green]Found mod ID: {mod_id}[/green]")
            return mod_id

    if results:
        nearest = ", ".join(f"{escape(title)} ({mod_id})" for mod_id, title in results[:3])
        console.print(
            f"[yellow]No Workshop item is titled {escape(mod_name)} — top results: {nearest}. "
            "Install it by workshop ID instead.[/yellow]"
        )
    else:
        console.print(f"[yellow]No ID found for {mod_name}[/yellow]")
    return None


def download_all_mods(
    config: DayzConfig,
    mod_list: list[str],
    cached_ids: dict[str, str],
    *,
    log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Download/update all mods from mod_list. Uses cached_ids as a name->ID lookup.

    Returns dict of {mod_name: status} where status is one of:
    'updated', 'new', 'unchanged', 'failed', 'skipped'.
    """
    from app.modmgr import modlist

    _emit = log or (lambda s: console.print(s))
    cached_names = set(cached_ids.keys())
    total = len(mod_list)
    results: dict[str, str] = {}

    _emit(f"\n[bold]Downloading {total} mod(s)...[/bold]\n")

    for i, mod_name in enumerate(mod_list, 1):
        # Lookup ID: try cache first, then web scrape
        mod_id = cached_ids.get(mod_name)
        if not mod_id:
            mod_id = resolve_mod_id(mod_name, cached_names)
            if mod_id:
                modlist.remember_id(config, mod_name, mod_id)
                cached_names.add(mod_name)

        if not mod_id:
            results[mod_name] = "skipped"
            continue

        mod_dir = config.workshop_dir / mod_id
        was_downloaded = mod_dir.is_dir()
        mtime_before = mod_dir.stat().st_mtime if was_downloaded else 0

        _emit(f"  [{i}/{total}] Downloading [cyan]{mod_name}[/cyan]...")
        try:
            download_mod(config, mod_id, mod_name, log=log)
        except Exception:
            results[mod_name] = "failed"
            continue

        mtime_after = mod_dir.stat().st_mtime if mod_dir.is_dir() else 0

        if not was_downloaded:
            results[mod_name] = "new"
        elif mtime_after > mtime_before:
            results[mod_name] = "updated"
        else:
            results[mod_name] = "unchanged"

    # Print summary — always a real Table on the CLI's console regardless of
    # `log`: a Rich Table can't be meaningfully rendered through a plain-text
    # log callback, so the web job log gets the per-mod lines above only.
    from rich.table import Table

    updated = [m for m, s in results.items() if s == "updated"]
    new = [m for m, s in results.items() if s == "new"]
    failed = [m for m, s in results.items() if s == "failed"]

    if updated or new or failed:
        console.print()
        table = Table(title="Mod Download Summary", expand=False)
        table.add_column("Mod", style="cyan")
        table.add_column("Status")
        for mod in new:
            table.add_row(mod, "[green]New[/green]")
        for mod in updated:
            table.add_row(mod, "[green]Updated[/green]")
        for mod in failed:
            table.add_row(mod, "[red]Failed[/red]")
        console.print(table)
    else:
        _emit(f"\n[dim]All {total} mods are up to date.[/dim]")

    console.print()
    return results
