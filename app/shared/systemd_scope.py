"""systemd scope abstraction — system units vs. per-user units.

The single entry point for *every* systemd-touching operation in zsm. Two
scopes are supported:

- **system** — units live in ``/etc/systemd/system`` and are driven with
  ``sudo systemctl …``. The historical behavior; still required for Docker
  (systemd as PID 1 + NOPASSWD sudo) and for hosts that already have a system
  unit installed.
- **user** — units live in ``~/.config/systemd/user`` and are driven with
  ``systemctl --user …``. **No sudo anywhere** — unit install is a plain file
  write in ``$HOME``. This is the new default on a normal Linux host, so the
  web UI (which has no tty to prompt for a sudo password) can manage the
  service. The only one-time root action is ``loginctl enable-linger <user>``
  so the user manager (and therefore the server + timers) runs at boot and
  survives logout.

Every new systemd feature MUST route through :func:`get_scope` and must never
hardcode ``sudo systemctl`` or ``/etc/systemd/system``.

Click-free so both ``app/web`` and ``app/commands`` can import it.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.shared.platform import (
    IS_ROOT,
    IS_WINDOWS,
    needs_sudo_auth,
    sudo_available_noninteractive,
    sudo_prefix,
)

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

# Canonical home for the unit name — service_control.py re-exports this to keep
# its many importers working (service_control imports systemd_scope, never the
# reverse, so the constant lives here to avoid an import cycle).
SERVICE_NAME = "dayz-server.service"
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")

# The instance that keeps the pre-instances unit names (plans/44 §3.3).
# `app.server.instances.DEFAULT_INSTANCE` is this, re-exported.
MAIN_INSTANCE = "main"


def _instance_of(config) -> str:
    ident = getattr(config, "instance_id", "")
    # A test's stand-in config has no real field; it is the legacy layout.
    return ident if isinstance(ident, str) and ident != MAIN_INSTANCE else ""


def server_unit(config: "DayzConfig | None" = None) -> str:
    """The server's unit for *config*'s instance (plans/44 step 6).

    `main`, and a config on the pre-instances layout, keep `SERVICE_NAME`:
    upgrading must not rename a unit that is running, enabled and named in
    the admin's own notes and timers. Every other instance gets
    `dayz-server-<id>.service`, a real file in `unit_dir` carrying its own
    paths and ports. `SERVICE_NAME` itself stays the name
    `resolve_service_scope` looks for to detect a legacy system install.
    """
    ident = _instance_of(config)
    return f"dayz-server-{ident}.service" if ident else SERVICE_NAME


def zsm_command(config: "DayzConfig | None", args: str) -> str:
    """``zsm <args>`` as the admin types it for *config*'s instance: a message
    that names a command has to name the one that reaches this instance."""
    ident = _instance_of(config)
    return f"zsm --instance {ident} {args}" if ident else f"zsm {args}"


def server_unit_description(config: "DayzConfig | None" = None) -> str:
    """The unit's ``Description=``, which `systemctl status` and the journal
    print. Another instance's names it; `main`'s stays as it always was, so an
    installed unit does not read as stale after an upgrade."""
    ident = _instance_of(config)
    return f"DayZ Dedicated Server ({ident})" if ident else "DayZ Dedicated Server"


def task_unit_prefix(config: "DayzConfig | None" = None) -> str:
    """What every scheduled-task unit of *config*'s instance starts with.

    `main` keeps `dayz-task-`. Another instance's are `dayz-task.<id>.`: the
    dot is in neither an instance id nor a task id, so no instance's prefix
    is the start of another's, and a timer install that removes "units no
    task owns" never reaches another instance's.
    """
    ident = _instance_of(config)
    return f"dayz-task.{ident}." if ident else "dayz-task-"
LINGER_DIR = Path("/var/lib/systemd/linger")

# Lets a deployment declare the scope its platform actually supports, without
# writing to config.json. Set to "system" by the Docker image: that container
# runs systemd as PID 1 with NOPASSWD sudo and cannot run user-scope services at
# all, so the heuristics below must never be allowed to guess "user" there.
SERVICE_SCOPE_ENV = "ZSM_SERVICE_SCOPE"


def _current_user() -> str:
    """Best-effort current username (env first, matches template rendering)."""
    return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()


def resolve_service_scope(config: "DayzConfig | None" = None) -> str:
    """Return ``"system"`` or ``"user"``.

    Resolution order:

    0. ``$ZSM_SERVICE_SCOPE`` if it is ``"system"``/``"user"`` — the platform
       declaring what it supports. It outranks the config because a stale or
       hand-set ``service_scope`` must not route a container (where user scope
       does not work at all) through ``systemctl --user``;
    1. explicit ``config.service_scope`` if it is ``"system"``/``"user"``;
    2. ``"system"`` if running as root (a root process manages system units);
    3. ``"system"`` if zsm's own system unit is already installed
       (``SYSTEM_UNIT_DIR / SERVICE_NAME`` carrying ``ZSM_UNIT_MARKER``) —
       existing installs keep working untouched until they migrate;
    4. otherwise ``"user"`` — the new default on a fresh systemd host.

    ``config=None`` loads the config lazily; a missing config file resolves via
    rules 2-4 without raising.
    """
    env_scope = (os.environ.get(SERVICE_SCOPE_ENV) or "").strip().lower()
    if env_scope in ("system", "user"):
        return env_scope

    if config is None:
        try:
            from app.shared.config import DayzConfig

            config = DayzConfig.load_or_none()
        except Exception:
            config = None

    explicit = (getattr(config, "service_scope", "") or "").strip().lower()
    if explicit in ("system", "user"):
        return explicit
    if IS_ROOT:
        return "system"
    if _zsm_system_unit():
        return "system"
    return "user"


def _zsm_system_unit() -> bool:
    """A system unit zsm wrote is installed (plans/78 D2): one the admin
    wrote says nothing about where zsm should install its own."""
    from app.server.unit_guard import is_zsm_unit

    try:
        return is_zsm_unit(
            (SYSTEM_UNIT_DIR / SERVICE_NAME).read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return False


@dataclass(frozen=True)
class SystemdScope:
    """A resolved systemd scope plus the mechanics that differ between them."""

    user_mode: bool

    @property
    def name(self) -> str:
        return "user" if self.user_mode else "system"

    @property
    def unit_dir(self) -> Path:
        """Directory unit files are installed into."""
        if self.user_mode:
            base = Path(
                os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
            ).expanduser()
            return base / "systemd" / "user"
        return SYSTEM_UNIT_DIR

    def systemctl_argv(self, *args: str, sudo: bool = True) -> list[str]:
        """Build a ``systemctl`` argv for this scope.

        User mode never uses sudo and injects ``--user``. System mode prefixes
        ``sudo`` unless ``sudo=False`` (read-only queries that don't need it).
        """
        if self.user_mode:
            return ["systemctl", "--user", *args]
        prefix = sudo_prefix() if sudo else []
        return [*prefix, "systemctl", *args]

    def subprocess_env(self) -> dict[str, str] | None:
        """Env to pass to systemctl subprocesses, or ``None`` to inherit.

        In user mode a headless process (the web server, a timer-launched
        oneshot, ``nohup zsm web``) may lack ``XDG_RUNTIME_DIR``, without which
        ``systemctl --user`` can't find the user bus. Inject it only when it is
        unset so an interactive session's real value is never clobbered.
        """
        if self.user_mode and not os.environ.get("XDG_RUNTIME_DIR"):
            try:
                uid = os.getuid()
            except AttributeError:  # pragma: no cover - Windows has no getuid
                return None
            return {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{uid}"}
        return None

    @property
    def needs_sudo(self) -> bool:
        """True only when a systemd op in this scope will invoke sudo."""
        if self.user_mode:
            return False
        return needs_sudo_auth()

    def query(self, *args: str, timeout: float = 5.0) -> str:
        """Run a read-only ``systemctl`` query; return stdout (``""`` if it failed).

        **Every** unit-state read goes through this — ``show ActiveState``,
        ``is-enabled``, ``MainPID``, timestamps. Unprivileged first: a state
        read should never need sudo.

        But in **system** scope a non-root process can only reach the systemd
        manager over the **D-Bus system bus** (root gets ``/run/systemd/private``
        directly). On a host without a system bus — notably a container built
        without the ``dbus`` package, since it is merely a *Recommends* of
        ``systemd`` — every unprivileged query fails with empty output. Empty is
        indistinguishable from "unit is inactive" to every caller upstream, so
        the app reported a running server as stopped: ``stop`` short-circuited
        with "Service already stopped" and the web status grid stayed grey while
        the server was live. Retrying through sudo keeps state truthful there.

        Two deliberate constraints on that retry:

        - ``sudo -n`` only, never bare ``sudo``. These run on hot poll paths in
          a tty-less web process; a password prompt would hang the poll.
        - It fires only on a *hard* failure (non-zero exit **and** no stdout).
          ``is-enabled`` exits non-zero while printing ``disabled``/``static``,
          which is a real answer, not a failure.
        """
        result = self._run_query(self.systemctl_argv(*args, sudo=False), timeout)
        if result is not None and (result.returncode == 0 or result.stdout.strip()):
            return result.stdout
        prefix = [] if self.user_mode else sudo_prefix()
        if not prefix:
            # User scope, or already root / no sudo — nothing sudo could add.
            return result.stdout if result is not None else ""
        retry = self._run_query([*prefix, "-n", "systemctl", *args], timeout)
        if retry is not None and (retry.returncode == 0 or retry.stdout.strip()):
            return retry.stdout
        return result.stdout if result is not None else ""

    def _run_query(
        self, cmd: list[str], timeout: float
    ) -> "subprocess.CompletedProcess[str] | None":
        """Run ``cmd`` capturing text output; ``None`` if it could not run."""
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=self.subprocess_env(),
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None


def get_scope(config: "DayzConfig | None" = None) -> SystemdScope:
    """Resolve and return the :class:`SystemdScope` for ``config``."""
    return SystemdScope(user_mode=resolve_service_scope(config) == "user")


def linger_enabled(username: str | None = None) -> bool:
    """True if ``loginctl enable-linger`` is active for the user.

    The linger marker file under ``/var/lib/systemd/linger`` is world-readable,
    so this needs no privileges.
    """
    if IS_WINDOWS:
        return False
    user = username or _current_user()
    return (LINGER_DIR / user).exists()


def user_bus_available() -> bool:
    """True if the per-user systemd manager is reachable.

    Probes ``systemctl --user is-system-running``: any exit code that still
    yields state on stdout (``running``/``degraded``/``starting``) means the
    bus answered; a "Failed to connect to bus" failure (no stdout) means it is
    not reachable (linger off and not logged in on a seat).
    """
    if IS_WINDOWS:
        return False
    scope = SystemdScope(user_mode=True)
    try:
        result = subprocess.run(
            scope.systemctl_argv("is-system-running"),
            capture_output=True,
            text=True,
            env=scope.subprocess_env(),
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def privileged_ops_available(
    config: "DayzConfig | None" = None,
) -> tuple[bool, str]:
    """Scope-aware gate for web endpoints that install/manage units.

    Returns ``(ok, reason)``. ``reason`` is empty when ``ok``; otherwise it is a
    user-facing, copy-pasteable message the caller surfaces (e.g. via HTTP 409).
    """
    scope = get_scope(config)
    if scope.user_mode:
        if user_bus_available():
            return True, ""
        return (
            False,
            "The per-user systemd bus is unreachable. Run: "
            f"sudo loginctl enable-linger {_current_user()}  — then restart zsm web.",
        )
    if sudo_available_noninteractive():
        return True, ""
    return (
        False,
        "This action needs sudo, but passwordless sudo is not configured. "
        "Configure NOPASSWD sudo for systemctl, or switch to user-scope "
        "services (service_scope=user).",
    )
