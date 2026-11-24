"""The revert timer behind ``--apply``'s confirm-or-revert net (plans/34 B).

The staged-rule gate (``firewall_ops``) proves the SSH allow rule **landed in
the table**. It cannot prove the operator is **still reachable**, and F13
measured the case where only the second fails: Ubuntu's ``ssh.socket`` binds a
bare ``ListenStream=2222`` IPv6-only, so ``2222/tcp`` is in the table, the gate
passes, and an IPv4 admin is locked out. The net is a revert armed *before* the
policy flip that undoes zsm's own change unless the operator confirms from the
box in time.

Three rules here are decisions, not details:

- **systemd owns the timer** (``systemd-run --on-active``), never a background
  ``sleep``/``nohup``/``setsid``. Those are children of the SSH session whose
  death is the event being protected against; the one teardown that kills them
  is the run that needed them.
- **System scope, always.** The revert runs the same privileged firewall
  commands ``--apply`` ran under sudo, and a user-scope timer dies with the user
  manager on a box without linger. So this module builds a system
  ``SystemdScope`` explicitly instead of resolving one from the config.
- **A fixed unit name.** A random name that leaks is invisible; a fixed one that
  is taken is a timer from an earlier run, which the operator needs to hear
  about — never retried under another name.

Imports nothing from ``firewall_ops`` — the dependency runs the other way.
No test may run a real ``systemd-run``/``systemctl``/``sudo`` or wait for a
real timer.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.shared.platform import IS_WINDOWS, sudo_prefix
from app.shared.systemd_scope import SystemdScope

UNIT = "zsm-firewall-revert"
TIMER = f"{UNIT}.timer"

#: How long the operator has to confirm. A judgement call, not a measurement:
#: answering honestly means opening a *second* session on the box and getting a
#: shell first, and ten seconds is not enough to do that. One constant so it can
#: be argued with.
CONFIRM_SECONDS = 120

DISARMED = "disarmed"
STILL_PENDING = "still-pending"
COULD_NOT_TELL = "could-not-tell"
#: The timer was already gone when we came to stop it — it fired (the revert
#: ran) or someone removed it. Either way the firewall may no longer be what
#: ``--apply`` left, and saying "disarmed" would be a lie.
ALREADY_GONE = "already-gone"

_SYSTEMD_BOOTED = Path("/run/systemd/system")


def _scope() -> SystemdScope:
    # Deliberately not get_scope(config) — see the module docstring.
    return SystemdScope(user_mode=False)


def stop_command() -> str:
    """What to type to stop a pending revert by hand."""
    return " ".join([*sudo_prefix(), "systemctl", "stop", TIMER])


def _timer_state(scope: SystemdScope) -> str | None:
    """``ActiveState`` of the timer, ``"not-found"`` when it is not loaded, or
    None when the read failed. Only ``active`` means a revert is pending."""
    out = scope.query("show", "-p", "LoadState", "-p", "ActiveState", TIMER)
    props = dict(
        line.split("=", 1) for line in out.splitlines() if "=" in line
    )
    if not props:
        return None
    if props.get("LoadState") == "not-found":
        return "not-found"
    return props.get("ActiveState") or None


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason: str = ""


def revert_timer_available(*, scope: SystemdScope | None = None, which=shutil.which) -> Availability:
    """Whether arming can be expected to work. Read-only; arms nothing.

    Called **before** ``--apply``'s confirmation, so the operator decides
    knowing whether the net is there.
    """
    if IS_WINDOWS:
        return Availability(False, "not a systemd host")
    if which("systemd-run") is None:
        return Availability(False, "systemd-run is not installed")
    if not _SYSTEMD_BOOTED.is_dir():
        return Availability(False, "systemd is not running as the init system")
    if _timer_state(scope or _scope()) == "active":
        return Availability(
            False,
            f"a revert timer from an earlier run ({TIMER}) is still pending — "
            f"inspect it with `systemctl list-timers {TIMER}`, "
            f"stop it with `{stop_command()}`",
        )
    return Availability(True)


@dataclass(frozen=True)
class ArmResult:
    ok: bool
    reason: str = ""
    stale: bool = False


def _exec_argv(commands: list[list[str]], which=shutil.which) -> list[str]:
    """One argv for ``systemd-run``, which takes a single command.

    Several commands become ``/bin/sh -c 'a && b'``. That looks like a quoting
    hazard and is not one: every word comes from ``build_revert_commands``' fixed
    templates plus a zone name firewalld itself reported, and each is
    ``shlex.quote``d anyway. Binaries are resolved to absolute paths now,
    because the timer's unit runs with systemd's PATH, not the operator's.
    """
    resolved = [[which(cmd[0]) or cmd[0], *cmd[1:]] for cmd in commands]
    if len(resolved) == 1:
        return resolved[0]
    return ["/bin/sh", "-c", " && ".join(shlex.join(cmd) for cmd in resolved)]


def arm_revert_timer(
    commands: list[list[str]], *, seconds: int = CONFIRM_SECONDS, run=subprocess.run, which=shutil.which
) -> ArmResult:
    """Arm a transient timer that runs *commands* in *seconds* unless disarmed.

    Lives in systemd, not in this process: it survives the SSH session dying,
    which is the whole point. It does **not** survive a reboot (transient units
    are not persisted) while the deny-by-default policy does — accepted and
    documented in plans/34.
    """
    argv = [
        *sudo_prefix(),
        "systemd-run",
        f"--unit={UNIT}",
        f"--on-active={int(seconds)}",
        "--timer-property=AccuracySec=1s",
        "--description=zsm: undo `service firewall --apply` unless confirmed",
        *_exec_argv(commands, which),
    ]
    try:
        proc = run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return ArmResult(False, f"could not run systemd-run: {exc}")
    if proc.returncode == 0:
        return ArmResult(True)
    err = (proc.stderr or proc.stdout or "").strip()
    if "already exists" in err or "already loaded" in err:
        return ArmResult(
            False,
            f"{TIMER} already exists — a revert from an earlier run is still "
            f"loaded. Inspect it with `systemctl list-timers {TIMER}`, stop it "
            f"with `{stop_command()}`",
            stale=True,
        )
    return ArmResult(False, err or f"systemd-run exited {proc.returncode}")


def disarm_revert_timer(*, scope: SystemdScope | None = None, run=subprocess.run) -> str:
    """Stop the pending revert, then **verify** it is gone.

    Returns one of ``DISARMED``, ``STILL_PENDING``, ``COULD_NOT_TELL`` or
    ``ALREADY_GONE`` — never a bool, because "could not tell" rendering as
    "disarmed" would print success over a firewall that turns itself off two
    minutes later. Same rule as the rest of ``app/ops/``: a read that failed is
    never an answer.

    The last window — the timer firing between the first read and the stop —
    reads as ``DISARMED``. The caller closes it by giving the prompt a deadline
    shorter than the timer's, so a yes can never arrive after the revert ran.
    """
    scope = scope or _scope()
    before = _timer_state(scope)
    if before is not None and before != "active":
        return ALREADY_GONE
    try:
        run(scope.systemctl_argv("stop", TIMER), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass  # the verify below is what decides
    after = _timer_state(scope)
    if after is None:
        return COULD_NOT_TELL
    if after in ("active", "activating", "reloading"):
        return STILL_PENDING
    return DISARMED
