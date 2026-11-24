"""The panel's own systemd unit, ``zsm-web.service`` (TODO A9).

``zsm web`` runs in the shell that started it and dies with the SSH session.
The deploy guide used to say "run it under tmux"; on a test VPS (2026 Q3)
that died silently, because tmux exits when ``zsm web`` does. What held there
was a hand-written user unit, and this module is that unit as a command:
``zsm service web install|remove``.

It goes through the server unit's machinery rather than a copy of it: rendered
from a template, rewritten for the per-user manager by ``_to_user_unit``,
installed into ``get_scope(config).unit_dir``. The unit name is the one that test
box used, so installing there replaces the hand-written file instead of running
a second panel beside it.

Click-free, so the doctor can import it.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from app.server.scheduler import _python_exe, _working_dir
from app.server.service_control import (
    ServiceResult,
    ServiceUserError,
    _render_template,
    _to_user_unit,
    _validate_unit_user,
    remove_service,
    systemctl,
    unit_value,
)
from app.shared.paths import INSTALL_DIR
from app.shared.platform import IS_WINDOWS, default_bind_host, has_systemd, sudo_prefix
from app.shared.systemd_scope import _current_user, get_scope

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

PANEL_SERVICE_NAME = "zsm-web.service"
PANEL_TEMPLATE_PATH = INSTALL_DIR / "templates" / "zsm-web.service.j2"
# `zsm web`'s default port. The unit runs it without --port.
PANEL_PORT = 8433
# How a refused path names this unit (``service_control.unit_value``).
_PANEL_UNIT = "the app's unit"


def render_panel_unit(config: "DayzConfig") -> str:
    """The unit text for this machine, in the resolved scope.

    ``ExecStart`` is ``"<python>" -m app web`` with the interpreter running now —
    the form the scheduler's task units use, right for a ``uv tool install`` and
    an editable checkout alike, and fixed at install time so the unit does not
    depend on the service manager's ``PATH``. The working directory comes from
    the same place, and it matters: see ``scheduler._working_dir``. Raises
    ``ServiceUserError`` in system scope when ``User=`` would name no account,
    and in either scope for a path ``unit_value`` refuses.
    """
    user = _current_user()
    # The template double-quotes the interpreter, so a path with a space stays
    # one word of ``ExecStart=`` (the server unit quotes its paths the same
    # way, 48654c4a). ``unit_value`` is the rule the task units share;
    # ``install_panel_unit`` reports its refusal.
    text = _render_template(
        PANEL_TEMPLATE_PATH,
        {
            "user": user,
            "python_path": unit_value("The Python interpreter", _python_exe(), _PANEL_UNIT),
            "working_dir": unit_value(
                "The working directory", str(_working_dir()), _PANEL_UNIT
            ),
        },
    )
    if get_scope(config).user_mode:
        return _to_user_unit(text)
    _validate_unit_user(user)
    return text


def panel_unit_path(config: "DayzConfig") -> Path:
    return get_scope(config).unit_dir / PANEL_SERVICE_NAME


def panel_bind_host(config: "DayzConfig") -> str:
    """Where the panel listens once systemd starts it. A unit has no SSH
    session, so the auto-detect that gives ``zsm web`` ``0.0.0.0`` in an SSH
    shell does not apply: without ``web_bind_host`` it is ``127.0.0.1``. In a
    Docker container it is ``0.0.0.0``, served over HTTPS with the self-signed
    certificate (``web.AUTO_TLS_BINDS``), since that detection needs no session."""
    return default_bind_host(config, env={})


def panel_state(config: "DayzConfig") -> dict:
    """``{installed, enabled, active}`` for the panel's unit; ``{}`` off systemd.

    ``active`` is ``systemctl is-active``'s word (``active``, ``failed``,
    ``activating``…), empty when the unit is not installed or could not be read.
    """
    if IS_WINDOWS or not has_systemd():
        return {}
    scope = get_scope(config)
    if not (scope.unit_dir / PANEL_SERVICE_NAME).exists():
        return {"installed": False, "enabled": False, "active": ""}
    return {
        "installed": True,
        "enabled": scope.query("is-enabled", PANEL_SERVICE_NAME).strip() == "enabled",
        "active": scope.query("is-active", PANEL_SERVICE_NAME).strip(),
    }


def panel_port_taken(config: "DayzConfig") -> bool:
    """Is something other than the panel's unit already on its port?

    The case this is for is the one the old guide produced: a ``zsm web``
    still running in tmux. The unit would fail to bind and restart every five
    seconds for ever. Only loopback is tried, so a panel bound to one LAN
    address alone goes unnoticed; that is a missed warning, not a wrong one.
    """
    if panel_state(config).get("active") == "active":
        return False
    try:
        with socket.create_connection(("127.0.0.1", PANEL_PORT), timeout=1):
            return True
    except OSError:
        return False


def panel_commands(config: "DayzConfig") -> tuple[str, str]:
    """``(restart, logs)`` command lines for the panel's unit, in its scope."""
    flag = "--user " if get_scope(config).user_mode else ""
    unit = PANEL_SERVICE_NAME.removesuffix(".service")
    return f"systemctl {flag}restart {unit}", f"journalctl {flag}-u {unit} -f"


def install_panel_unit(config: "DayzConfig") -> ServiceResult:
    """Write the unit, daemon-reload, enable it at boot and (re)start it.

    Restart rather than start, so reinstalling after an upgrade runs the new
    unit instead of leaving the old process up.
    """
    try:
        content = render_panel_unit(config)
    except ServiceUserError as e:
        return ServiceResult(False, str(e))
    scope = get_scope(config)
    dest = scope.unit_dir / PANEL_SERVICE_NAME
    try:
        if scope.user_mode:
            scope.unit_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        else:
            subprocess.run(
                [*sudo_prefix(), "tee", str(dest)],
                input=content,
                text=True,
                stdout=subprocess.DEVNULL,
                check=True,
            )
    except (OSError, subprocess.CalledProcessError) as e:
        return ServiceResult(False, f"Failed to write {dest}: {e}")
    for action, unit in (("daemon-reload", False), ("enable", True), ("restart", True)):
        result = systemctl(action, unit=unit, config=config, name=PANEL_SERVICE_NAME)
        if not result.ok:
            return result
    return ServiceResult(True)


def remove_panel_unit(config: "DayzConfig") -> ServiceResult:
    """Stop, disable and delete the panel's unit."""
    return remove_service(config, name=PANEL_SERVICE_NAME)
