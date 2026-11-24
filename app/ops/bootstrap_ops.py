"""Take a bare Ubuntu VPS to "ready to run `zsm setup`" (TODO A3).

**This is the only place provisioning logic lives.** The cloud-init template
(A8) and any Ansible role call `zsm bootstrap-vps`; they do not reimplement
it, or the provisioning story exists twice and drifts.

The shape follows `firewall_ops`, because the hazard is the same — a mistake
here is on someone's only machine:

- **Planning never mutates.** `plan()` inspects the host and returns
  `BootstrapStep`s carrying the exact argv and file contents that *would* run;
  the only things that change anything are `_run_command` and `_write_file`,
  and `plan()` calls neither. It is **not** side-effect free — it shells out
  to read (`ufw status` via `firewall_status`, `timedatectl show` via
  `clock_synced`) — so don't write "pure" here; a test asserting that
  caught the claim. Every rule below is assertable without a privileged host,
  and no test in this repo may run `useradd`, `sysctl`, `swapon` or
  `fallocate`.
- **Nothing happens without `--apply`.** Reporting is the default, which is the
  convention `zsm service firewall` set. TODO A3 asked for `--dry-run`; that
  spelling works too and means the same as the default.
- **Idempotent by construction.** A step that inspects its way to "already
  done" carries no commands, so a second run is a no-op that says so rather
  than a second `useradd`.
- **"Could not tell" is not "needed".** A probe that fails leaves the step
  `unknown` with the reason, and `--apply` skips it. Guessing would mean
  writing to a host we could not read.

**Firewall is deliberately not done here by default.** `firewall_ops.apply_rules`
owns the staged-rule lockout gate, which is the single most dangerous thing
zsm can do; A3 calls *that function* behind its own `--firewall` flag rather
than owning a second copy of the reasoning. Without the flag the plan just
says to run `zsm service firewall --apply`.

Package installation is **not** here either: that is A2, and it lives in
`install.sh` + `app/shared/os_packages.py`.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.ops import vps_readiness
from app.shared.platform import IS_DOCKER, IS_ROOT, IS_WINDOWS, sudo_prefix

#: Step is satisfied already — no commands, and `--apply` skips it.
OK = "ok"
#: Step has work to do.
NEEDED = "needed"
#: Step does not apply to this host (not root, no systemd, …).
SKIPPED = "skipped"
#: A probe failed. **Never treated as NEEDED** — see the module docstring.
UNKNOWN = "unknown"

#: Where zsm's swapfile goes, and how big. 4 GB is the figure
#: `vps_readiness.swapfile_gb_on` already reasons about (its F3 note measured a
#: 4 GB swapfile taking a 40 GB disk from 34.3 GB free to 29.5 GB), so the two
#: agree about the same file rather than each assuming a size.
SWAPFILE = Path("/swapfile")
SWAP_SIZE_GB = 4

#: zsm's drop-ins. One name everywhere so a re-run overwrites its own file
#: rather than appending a second block, and so removing them is obvious.
SYSCTL_DROPIN = Path("/etc/sysctl.d/99-zsm.conf")
LIMITS_DROPIN = Path("/etc/security/limits.d/99-zsm.conf")
SYSTEMD_SYSTEM_DROPIN = Path("/etc/systemd/system.conf.d/99-zsm.conf")
SYSTEMD_USER_DROPIN = Path("/etc/systemd/user.conf.d/99-zsm.conf")


@dataclass
class BootstrapStep:
    key: str
    label: str
    state: str
    detail: str
    #: argv lists, run in order. Empty unless ``state == NEEDED``.
    commands: list[list[str]] = field(default_factory=list)
    #: ``(path, content)`` written before ``commands`` run.
    files: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return self.state == NEEDED


@dataclass
class BootstrapPlan:
    steps: list[BootstrapStep]
    #: Things the operator must know that are not steps.
    notes: list[str] = field(default_factory=list)
    #: Set when the whole plan is refused (Windows, container).
    refusal: str | None = None

    @property
    def actionable(self) -> list[BootstrapStep]:
        return [s for s in self.steps if s.actionable]


def display_command(argv: list[str]) -> str:
    """*argv* as one shell line that, pasted, runs exactly *argv*.

    Every command the report prints is one an operator may paste instead of
    passing `--apply`. A plain ``" ".join`` dropped the quotes around ``sh
    -c``'s argument, so the fstab line pasted as ``sh -c grep`` followed by an
    unprivileged ``>> /etc/fstab``. Each argument is quoted as ``shlex.quote``
    would, except that one holding single quotes goes in double quotes when
    nothing in it is special there (``!`` included, for an interactive bash or
    zsh), so the line reads as written rather than as ``'"'"'`` escapes.
    """

    def quote(arg: str) -> str:
        if "'" in arg and not set(arg) & set('"$`\\!'):
            return f'"{arg}"'
        return shlex.quote(arg)

    return " ".join(quote(arg) for arg in argv)


def _sudo(argv: list[str]) -> list[str]:
    """Prefix with sudo unless already root (or it is unavailable)."""
    return [*sudo_prefix(), *argv]


def current_user() -> str:
    from app.shared.systemd_scope import _current_user

    return _current_user()


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _step_user(username: str | None) -> BootstrapStep:
    """Create the non-root user a DayZ server should run as.

    Only meaningful **as root** — that is the cloud-init case, where the only
    account is `root`. Run as an ordinary user, zsm is already where it should
    be and creating a second account would be surprising.

    **Root's `authorized_keys` is copied to the new user.** Without it a fresh
    VPS hands you a user you cannot log in as, and the natural next step —
    disabling root SSH — locks the box. This is the part that is easy to leave
    out and impossible to recover from remotely.
    """
    if not IS_ROOT:
        return BootstrapStep(
            "user", "Non-root user", SKIPPED,
            f"Already running as {current_user()!r}, which is what the server should run as.",
        )
    if not username:
        return BootstrapStep(
            "user", "Non-root user", UNKNOWN,
            "Running as root with no --user given. Pass --user <name> to create one.",
        )
    if Path("/home", username).is_dir() or _user_exists(username):
        return BootstrapStep(
            "user", "Non-root user", OK, f"User {username!r} already exists."
        )

    admin_group = "wheel" if Path("/etc/redhat-release").exists() else "sudo"
    commands = [
        ["useradd", "--create-home", "--shell", "/bin/bash", username],
        ["usermod", "--append", "--groups", admin_group, username],
    ]
    root_keys = Path("/root/.ssh/authorized_keys")
    if root_keys.is_file():
        commands += [
            ["install", "-d", "-m", "700", "-o", username, "-g", username,
             f"/home/{username}/.ssh"],
            ["install", "-m", "600", "-o", username, "-g", username,
             str(root_keys), f"/home/{username}/.ssh/authorized_keys"],
        ]
        detail = f"Create {username!r} in {admin_group!r} and copy root's SSH keys to it."
    else:
        # Say so loudly: creating a user with no way in is worse than not
        # creating one, and the operator can still add a key before switching.
        detail = (
            f"Create {username!r} in {admin_group!r}. **root has no authorized_keys "
            "to copy** — add an SSH key for this user before you disable root login."
        )
    return BootstrapStep("user", "Non-root user", NEEDED, detail, commands)


def _user_exists(username: str) -> bool:
    try:
        import pwd

        pwd.getpwnam(username)
        return True
    except (KeyError, ImportError):
        return False


def _step_linger(username: str) -> BootstrapStep:
    """`loginctl enable-linger` — user services survive logout.

    This is the one root action a user-scope install needs, and without it the
    server stops when the SSH session ends, which reads as a crash.
    """
    from app.shared.systemd_scope import linger_enabled

    if shutil.which("loginctl") is None:
        return BootstrapStep(
            "linger", "User service lingering", SKIPPED,
            "No loginctl on this host (not systemd) — nothing to enable.",
        )
    if linger_enabled(username):
        return BootstrapStep(
            "linger", "User service lingering", OK,
            f"Lingering is already on for {username!r}.",
        )
    return BootstrapStep(
        "linger", "User service lingering", NEEDED,
        f"Enable lingering for {username!r} so its services keep running after logout.",
        [_sudo(["loginctl", "enable-linger", username])],
    )


def _step_swap() -> BootstrapStep:
    """Add a swapfile on a small box.

    The rule is `SWAP_REQUIRED_BELOW_MB` from `app/data/vps-thresholds.env` —
    the same number doctor and `preflight.sh` judge against, measured in
    **advertised** MB (a box sold as 4 GB reports ~3.8 GB; see
    `advertised_ram_mb`).

    **Checked against free disk first, the way doctor counts it.** Since F3
    (`vps_readiness.swapfile_gb_on`) the disk check credits a swapfile back, so
    adding one never fails that check. Refused only when the disk is short
    already: then the 4 GB would come out of space the server needs. Comparing
    free disk *minus* the swapfile, as this once did, refused swap
    on every 40 GB box (measured on a VPS: 33.6 GB free), the entry plan
    the swap rule exists for.
    """
    thresholds = vps_readiness.thresholds_or_defaults()
    measured, _free = vps_readiness.mem_mb()
    ram = vps_readiness.advertised_ram_mb(measured)
    if ram is None:
        return BootstrapStep("swap", "Swap", UNKNOWN, "Could not read total RAM.")
    if ram >= thresholds["SWAP_REQUIRED_BELOW_MB"]:
        return BootstrapStep(
            "swap", "Swap", OK,
            f"{ram} MB RAM is at or above the {thresholds['SWAP_REQUIRED_BELOW_MB']} MB "
            "threshold — no swap needed.",
        )
    if _swap_total_kb() > 0:
        return BootstrapStep("swap", "Swap", OK, "Swap is already active.")
    if SWAPFILE.exists():
        return BootstrapStep(
            "swap", "Swap", UNKNOWN,
            f"{SWAPFILE} already exists but is not in use — inspect it by hand rather "
            "than letting Zedstead overwrite a file it did not create.",
        )

    free_gb = vps_readiness.disk_free_gb(Path("/"))
    if free_gb is None:
        return BootstrapStep("swap", "Swap", UNKNOWN, "Could not read free disk space.")
    if free_gb < thresholds["MIN_DISK_GB"]:
        return BootstrapStep(
            "swap", "Swap", UNKNOWN,
            f"Only {free_gb:.1f} GB free, under the {thresholds['MIN_DISK_GB']} GB the "
            f"disk check wants, so a {SWAP_SIZE_GB} GB swapfile would come out of space "
            "the server needs. Free space or resize the box first.",
        )

    return BootstrapStep(
        "swap", "Swap", NEEDED,
        f"{ram} MB RAM and no swap — add a {SWAP_SIZE_GB} GB swapfile at {SWAPFILE}.",
        [
            _sudo(["fallocate", "-l", f"{SWAP_SIZE_GB}G", str(SWAPFILE)]),
            _sudo(["chmod", "600", str(SWAPFILE)]),
            _sudo(["mkswap", str(SWAPFILE)]),
            _sudo(["swapon", str(SWAPFILE)]),
            # Survives a reboot. `--append-lines` would duplicate on a re-run;
            # this step only runs when there is no swapfile at all.
            _sudo(["sh", "-c", f"grep -q '^{SWAPFILE} ' /etc/fstab || "
                               f"echo '{SWAPFILE} none swap sw 0 0' >> /etc/fstab"]),
        ],
    )


def _swap_total_kb() -> int:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        if line.startswith("SwapTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return 0


def _step_file_handles(username: str) -> BootstrapStep:
    """Raise host-level file-handle limits.

    **Host level only.** The server unit already carries
    `LimitNOFILE=65536` (`app/templates/dayz-server.service.j2`), and TODO A3
    says so explicitly — this is about `fs.file-max`, the login session's
    rlimit, and the ceiling a systemd-launched `zsm web` inherits.

    Three files, because they are three different mechanisms and setting one
    does not set the others: `sysctl.d` is the kernel-wide count,
    `limits.d` is PAM login sessions, and systemd's `*.conf.d` is what a unit
    inherits — `limits.conf` does **not** reach a systemd-started process, so
    without the last two the web panel keeps the 1024 soft default.
    """
    thresholds = vps_readiness.thresholds_or_defaults()
    file_max, _soft, hard = vps_readiness.file_handle_limits()
    want_file_max = thresholds["MIN_FILE_MAX"]
    want_nofile = thresholds["MIN_NOFILE"]

    if file_max is None and hard is None:
        return BootstrapStep(
            "file_handles", "File handle limits", UNKNOWN,
            "Could not read fs.file-max or the process rlimit.",
        )

    file_max_low = file_max is not None and file_max < want_file_max
    nofile_low = hard is not None and hard < want_nofile
    if not file_max_low and not nofile_low:
        return BootstrapStep(
            "file_handles", "File handle limits", OK,
            f"fs.file-max {vps_readiness._fmt_limit(file_max)} and hard nofile "
            f"{vps_readiness._fmt_limit(hard)} are both at or above the minimum.",
        )

    files = [
        (SYSCTL_DROPIN, f"# Written by zsm bootstrap-vps\nfs.file-max = {want_file_max}\n"),
        (LIMITS_DROPIN,
         "# Written by zsm bootstrap-vps\n"
         f"{username} soft nofile {want_nofile}\n"
         f"{username} hard nofile {want_nofile}\n"),
        (SYSTEMD_SYSTEM_DROPIN,
         f"# Written by zsm bootstrap-vps\n[Manager]\nDefaultLimitNOFILE={want_nofile}\n"),
        (SYSTEMD_USER_DROPIN,
         f"# Written by zsm bootstrap-vps\n[Manager]\nDefaultLimitNOFILE={want_nofile}\n"),
    ]
    reasons = []
    if file_max_low:
        reasons.append(f"fs.file-max is {file_max} (want {want_file_max})")
    if nofile_low:
        reasons.append(
            f"hard nofile is {vps_readiness._fmt_limit(hard)} (want {want_nofile})"
        )
    return BootstrapStep(
        "file_handles", "File handle limits", NEEDED,
        "; ".join(reasons) + ". A reboot (or a re-login) is needed for the rlimit.",
        [_sudo(["sysctl", "--system"]), _sudo(["systemctl", "daemon-reexec"])],
        files,
    )


def _step_timesync() -> BootstrapStep:
    """Turn on NTP. Steam auth and the A2S query both dislike a clock skew."""
    if shutil.which("timedatectl") is None:
        return BootstrapStep(
            "timesync", "Clock sync", SKIPPED,
            "No timedatectl on this host — set up NTP with whatever this distro uses.",
        )
    synced = vps_readiness.clock_synced()
    if synced is True:
        return BootstrapStep("timesync", "Clock sync", OK, "The clock is NTP-synchronised.")
    if synced is None:
        return BootstrapStep(
            "timesync", "Clock sync", UNKNOWN, "timedatectl did not report a sync state."
        )
    return BootstrapStep(
        "timesync", "Clock sync", NEEDED,
        "The clock is not NTP-synchronised — Steam auth and the server browser both "
        "dislike a skew.",
        [_sudo(["timedatectl", "set-ntp", "true"])],
    )


def _step_firewall(config, *, include: bool) -> BootstrapStep:
    """Report the firewall; only *act* when explicitly asked.

    `firewall_ops.apply_rules` owns the staged-rule verification gate that
    keeps `--apply` from locking the operator out of their own box. A3 calls
    that function rather than carrying a second copy of the reasoning, and it
    stays behind its own flag because deny-by-default is the one step here
    that can end the session.
    """
    from app.ops import firewall_ops

    status = firewall_ops.firewall_status(config)
    if not status.backend:
        return BootstrapStep(
            "firewall", "Firewall", SKIPPED, firewall_ops.NO_BACKEND_SUMMARY
        )
    if firewall_ops.already_applied(config, status):
        return BootstrapStep(
            "firewall", "Firewall", OK,
            f"{status.backend} is on and deny-by-default, with SSH and the server's "
            "ports allowed.",
        )
    if not include:
        return BootstrapStep(
            "firewall", "Firewall", SKIPPED,
            f"{status.backend} detected. Not touched — run "
            "`zsm service firewall --apply` yourself, or re-run this with --firewall.",
        )
    return BootstrapStep(
        "firewall", "Firewall", NEEDED,
        f"Hand over to `zsm service firewall --apply` ({status.backend}), which "
        "verifies the SSH rule landed before denying anything else.",
    )


# ---------------------------------------------------------------------------
# Plan + apply
# ---------------------------------------------------------------------------


def plan(config, *, username: str | None = None, firewall: bool = False) -> BootstrapPlan:
    """Inspect the host and describe what bootstrapping it would do.

    **Reads only.** Some of those reads are subprocesses (`ufw status`,
    `timedatectl show`) — read-only ones — but nothing here writes a file or
    runs a command from a step. ``username`` is who the server will run as:
    the account to create when running as root, and the account whose limits
    and lingering are configured either way.
    """
    if IS_WINDOWS:
        return BootstrapPlan([], refusal="bootstrap-vps provisions a Linux host.")
    if IS_DOCKER:
        return BootstrapPlan(
            [],
            refusal=(
                "Refusing inside a container: swap, sysctl and file-handle limits "
                "belong to the Docker host, and changing them here would either fail "
                "or change the host for every container on it."
            ),
        )

    target = username or (None if IS_ROOT else current_user())
    steps = [_step_user(username)]
    if target:
        steps.append(_step_linger(target))
    else:
        steps.append(
            BootstrapStep(
                "linger", "User service lingering", UNKNOWN,
                "No target user yet — pass --user <name>.",
            )
        )
    steps.append(_step_swap())
    steps.append(_step_file_handles(target or "root"))
    steps.append(_step_timesync())
    steps.append(_step_firewall(config, include=firewall))

    notes = []
    if IS_ROOT and not username:
        notes.append(
            "Running as root with no --user: Zedstead cannot tell which account the server "
            "should belong to, so the user and lingering steps are skipped."
        )
    notes.append(
        "OS packages are not bootstrapped here — that is "
        "`sh install.sh --yes`, which shares its package list with the "
        "Dockerfile and `zsm doctor`."
    )
    notes.append("Next: `zsm init` (or `zsm setup` for a headless install).")
    return BootstrapPlan(steps, notes)


def apply(
    bootstrap_plan: BootstrapPlan,
    config,
    *,
    log: Callable[[str], None] = print,
    run: Callable[[list[str]], int] | None = None,
    write: Callable[[Path, str], None] | None = None,
    assume_yes: bool = False,
    confirm: Callable[[list[str]], bool] | None = None,
) -> int:
    """Execute the actionable steps. Returns an exit code.

    ``run`` and ``write`` are injected so a test can assert the sequence
    without a privileged host — the same reason `vps_readiness` injects
    `_sleep` and `_device_of`. The defaults are the real thing.

    ``assume_yes`` and ``confirm`` are **forwarded to
    `firewall_ops.apply_rules`, not consumed here.** That function documents
    that omitting a confirm while ``assume_yes`` is False is itself a refusal,
    "so a caller that forgets to wire up a prompt cannot apply by accident" —
    passing a hardcoded ``assume_yes=True`` would have quietly disarmed it.
    A3's own prompt is the CLI's; this one is the firewall's.
    """
    if bootstrap_plan.refusal:
        log(bootstrap_plan.refusal)
        return 1

    runner = run or _run_command
    writer = write or _write_file
    failures = 0

    for step in bootstrap_plan.actionable:
        log(f"\n{step.label}: {step.detail}")
        if step.key == "firewall":
            # Delegated on purpose — see _step_firewall.
            from app.ops import firewall_ops

            code = firewall_ops.apply_rules(
                config, assume_yes=assume_yes, log=log, confirm=confirm
            )
            if code != 0:
                failures += 1
            continue

        for path, content in step.files:
            log(f"  write {path}")
            try:
                writer(path, content)
            except Exception as e:
                log(f"  failed: {e}")
                failures += 1
                break
        else:
            for argv in step.commands:
                log(f"  $ {display_command(argv)}")
                code = runner(argv)
                if code != 0:
                    log(f"  failed (exit {code})")
                    failures += 1
                    break

    if failures:
        log(f"\n{failures} step(s) failed. Re-run to retry — every step is idempotent.")
        return 1
    return 0


def _run_command(argv: list[str]) -> int:
    import subprocess

    try:
        # stdin=DEVNULL: this runs under cloud-init and under a timer as often
        # as at a terminal, and nothing here may block on a prompt. sudo still
        # reads its password from /dev/tty when there is one.
        return subprocess.run(argv, stdin=subprocess.DEVNULL).returncode
    except OSError as e:
        raise RuntimeError(f"could not run {argv[0]}: {e}") from e


def _write_file(path: Path, content: str) -> None:
    """Write a root-owned drop-in, via sudo when not already root."""
    if IS_ROOT:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o644)
        return
    import subprocess

    subprocess.run(_sudo(["mkdir", "-p", str(path.parent)]), check=True,
                   stdin=subprocess.DEVNULL)
    # `sudo tee`, not a shell redirect: the redirect happens in *our* shell,
    # which is not root, so `sudo echo … > /etc/…` fails on a real box.
    proc = subprocess.run(
        _sudo(["tee", str(path)]), input=content, text=True,
        stdout=subprocess.DEVNULL, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not write {path}")
