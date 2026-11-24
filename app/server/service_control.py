"""Pure server-lifecycle primitives: systemctl wrapper, Windows process
control, service/start-script template rendering, and mod-load-order
registry merging.

Extracted from ``app/commands/service.py`` so ``app/web`` (and any other
headless caller) can drive service start/stop/restart/status without
importing Click or Rich. ``app/commands/service.py`` re-imports every name
below and keeps its own CLI-flavored ``_systemctl`` / ``_windows_stop_server``
/ ``_windows_server_status`` wrappers that add interactive/Rich printing on
top of the structured ``ServiceResult`` these return.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.modmgr.mods import breaks_a_line, split_name_id
from app.shared.paths import INSTALL_DIR
from app.shared.platform import IS_WINDOWS, has_systemd, sudo_prefix
from app.modmgr import modlist
from app.shared.seed_data import WORKSHOP_MOD_IDS
from app.shared.systemd_scope import (
    SERVICE_NAME,  # re-exported: importers of service_control still use it
    SYSTEM_UNIT_DIR,
    _current_user,
    zsm_command,
    get_scope,
    server_unit,
    server_unit_description,
)

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

log = logging.getLogger(__name__)

SERVER_BINARY = "DayZServer"
# ERE matched against the full command line by `pgrep -f`. Anchored to a path
# separator or start-of-string on the left and whitespace or end-of-string on
# the right so it hits `./DayZServer -config=...` and `/opt/dz/DayZServer_x64`
# but not `tail -f DayZServer.RPT`.
DAYZ_PROCESS_PATTERN = rf"(^|/){SERVER_BINARY}(_x64)?([[:space:]]|$)"
# Back-compat alias — the canonical system unit dir now lives in
# systemd_scope. System-scope call sites still reference SYSTEMD_DIR; user
# scope resolves its unit dir through get_scope(config).unit_dir instead.
SYSTEMD_DIR = SYSTEM_UNIT_DIR
TEMPLATE_PATH = INSTALL_DIR / "templates" / "dayz-server.service.j2"
START_BAT_TEMPLATE_PATH = INSTALL_DIR / "templates" / "start-server.bat.j2"
STOP_BAT_TEMPLATE_PATH = INSTALL_DIR / "templates" / "stop-server.bat.j2"

START_BAT_NAME = "start-server.bat"
STOP_BAT_NAME = "stop-server.bat"

NO_SYSTEMD_MESSAGE = (
    "systemd is not available here — the DayZ service can't be managed. "
    "Run the server directly, or manage it with your container / supervisor "
    "(e.g. 'docker compose restart')."
)


@dataclass
class ServiceResult:
    ok: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Mod load order / template rendering (pure — no prints, no subprocess)
# ---------------------------------------------------------------------------

def _parse_workshop_registry(text: str) -> list[str]:
    """Returns mod names in the exact order defined in WORKSHOP_MOD_IDS."""
    mods = []
    for line in text.splitlines():
        entry = split_name_id(line)
        if entry:
            mods.append(entry[0])
    return mods


def build_registry_order(config: "DayzConfig") -> list[str]:
    """Merge the mod list's pinned load order with the WORKSHOP_MOD_IDS registry.

    Custom-ordered mods get lower indices (higher tiebreaker priority).
    Mods only in the registry follow after, deduplicated.
    """
    registry = _parse_workshop_registry(WORKSHOP_MOD_IDS)
    custom = modlist.load(config).pinned
    if not custom:
        return registry
    seen = set(custom)
    return list(custom) + [m for m in registry if m not in seen]


def _ordered_active_mods(config: "DayzConfig") -> list[str]:
    """Every enabled mod, in load order: `mods.get_resolved_mod_order`, which
    sorts by dependencies or, in manual mode, keeps the saved order (plans/49)."""
    from app.modmgr.mods import get_resolved_mod_order

    return get_resolved_mod_order(config)


def _get_mod_string(config: "DayzConfig") -> str:
    """The ordered ``-mod=`` list: every enabled mod not marked server-side."""
    server_side = set(modlist.server_only(config))
    return ";".join(m for m in _ordered_active_mods(config) if m not in server_side)


def _get_server_mod_parts(config: "DayzConfig") -> tuple[str, str]:
    """``(-serverMod= list, server_args to emit)`` for the launch command.

    With nothing marked server-side this returns ``("", config.server_args)``
    unchanged. A ``-serverMod=`` typed into the launch flags then stays where it
    was, and an install that never uses the feature keeps a byte-identical
    ``ExecStart`` (plans/47 A). Otherwise the typed entries are taken out of the
    flags and merged after the generated ones into **one** flag, deduplicated,
    because two ``-serverMod=`` on one command line leave the engine to pick one.
    A server-side mod that is disabled is left out, like it is from ``-mod=``.
    """
    server_side = set(modlist.server_only(config))
    if not server_side:
        return "", config.server_args
    from app.shared.server_args import split_server_mod_flag

    generated = [m for m in _ordered_active_mods(config) if m in server_side]
    if not generated:
        return "", config.server_args
    typed, rest = split_server_mod_flag(config.server_args)
    return ";".join(dict.fromkeys(generated + typed)), rest


RESTART_POLICIES = ("always", "on-failure", "no")
# systemd takes a time span here; anything outside this is a typo rather than
# an intention. A delay of 0 would spin a failing unit as fast as the CPU
# allows, and an hour is already far past "the box will fix itself".
RESTART_SECONDS_MIN = 1
RESTART_SECONDS_MAX = 3600


def resolve_restart_policy(value: str) -> str:
    """Config value → a `Restart=` systemd will accept.

    Validated **at render time, not at save time**: a unit systemd refuses to
    parse leaves the box with no way to start the server at all, so an
    unrecognised value falls back to today's behaviour instead of being
    written out. The config keeps whatever was typed; only the unit is sane.
    """
    return value if value in RESTART_POLICIES else "always"


def resolve_restart_seconds(value: int) -> int:
    """Config value → a `RestartSec=` within reach of being meant."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 30
    return max(RESTART_SECONDS_MIN, min(seconds, RESTART_SECONDS_MAX))


def _path_arg(value: str) -> str:
    """A path as the launch command passes it, inside double quotes.

    On Windows its trailing backslashes are dropped: the server's own argv
    parser reads a backslash before the closing quote as a literal quote, so
    ``"-profiles=C:\\DayZ\\"`` would run into the next argument. A drive root
    has nothing it can safely drop (``C:`` is that drive's current directory),
    so `_validate_launch_command` refuses one. POSIX values are unchanged.
    """
    return value.rstrip("\\") if IS_WINDOWS else value


_DRIVE_ROOT = re.compile(r"[A-Za-z]:")


def _get_template_replacements(config: "DayzConfig") -> dict[str, str]:
    """Build the template variable replacements dict."""
    mod_string = _get_mod_string(config)
    server_mod_string, server_args = _get_server_mod_parts(config)
    policy = resolve_restart_policy(config.service_restart_policy)
    if policy != config.service_restart_policy:
        log.warning(
            "Unknown service_restart_policy %r — writing Restart=%s. Valid: %s",
            config.service_restart_policy, policy, ", ".join(RESTART_POLICIES),
        )
    restart_sec = resolve_restart_seconds(config.service_restart_seconds)
    if restart_sec != config.service_restart_seconds:
        log.warning(
            "service_restart_seconds %r is out of range — writing RestartSec=%s.",
            config.service_restart_seconds, restart_sec,
        )
    return {
        # Must resolve to a real account: a User= systemd can't resolve fails the
        # unit at runtime with 217/USER and no log of its own. $USER is absent
        # under `docker compose exec`, so the old "dayz" literal fallback wrote a
        # unit naming an account that never existed. _current_user() falls back
        # to a passwd lookup instead of a guess.
        "user": _current_user(),
        "description": server_unit_description(config),
        "server_dir": config.server_dir,
        "python_path": sys.executable,
        "config_path": _path_arg(config.config_path),
        "mod_string": mod_string,
        "server_mod_string": server_mod_string,
        # The engine treats an empty -mod= as a malformed mod list, so the flag
        # is dropped entirely when no mods are active (8a34696); -serverMod=
        # follows the same rule. Both ride in one template slot so a unit with
        # no server-side mods renders byte for byte as it did before.
        "mod_arg": " ".join(
            arg
            for arg in (
                f'"-mod={mod_string}"' if mod_string else "",
                f'"-serverMod={server_mod_string}"' if server_mod_string else "",
            )
            if arg
        ),
        "profiles_path": _path_arg(str(config.server_dir_path / config.profiles_path)),
        "battleye_path": _path_arg(str(config.server_dir_path / config.battleye_path)),
        "server_args": server_args,
        "server_port": str(config.server_port),
        # Either at 0 means "no brake", and systemd spells that
        # StartLimitIntervalSec=0 — a burst of 0 on its own does not disable
        # it, so both are normalised to the interval here.
        "start_limit_interval": str(
            config.restart_limit_interval_seconds if config.restart_limit_burst else 0
        ),
        "start_limit_burst": str(max(config.restart_limit_burst, 0)),
        "restart": policy,
        "restart_sec": str(restart_sec),
    }


class ServiceUserError(ValueError):
    """The unit's ``User=`` does not name an account that exists here.

    Raised at generate time on purpose: systemd rejects such a unit at *runtime*
    with ``217/USER`` and, under ``Restart=always``, crash-loops it forever
    while the service itself logs nothing about the cause.
    """


def _validate_unit_user(user: str) -> None:
    """Fail loudly if ``user`` can't be resolved to a local account."""
    if IS_WINDOWS:
        return
    try:
        import pwd
    except ImportError:  # pragma: no cover — non-POSIX
        return
    if not user:
        raise ServiceUserError(
            "Cannot determine which user the DayZ server should run as: "
            "$USER is empty and no account matches this process. "
            "Set $USER (or run via `runuser -u <user> -- env USER=<user> ...`) "
            "before generating the service."
        )
    try:
        pwd.getpwnam(user)
    except KeyError:
        raise ServiceUserError(
            f"The service would run as user {user!r}, which does not exist on "
            "this system. systemd would fail the unit with 217/USER and restart "
            "it in a loop. Generate the service as the account that owns the "
            "server directory, or set $USER to it."
        ) from None


def _render_template(template_path: Path, replacements: dict[str, str]) -> str:
    """Render a template file with {{ key }} replacements."""
    result = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        result = result.replace("{{ " + key + " }}", value)
    return result


def _to_user_unit(text: str) -> str:
    """Rewrite a rendered system unit for the per-user systemd manager.

    User units run as the owning user, so ``User=`` is invalid and
    ``multi-user.target`` doesn't exist in the user manager — enable-at-boot
    would silently never fire. ``After=network-online.target`` is left as-is
    (ordering-only; harmless when the referenced unit is absent).
    """
    kept = [ln for ln in text.splitlines() if not ln.startswith("User=")]
    result = "\n".join(kept)
    if text.endswith("\n"):
        result += "\n"
    return result.replace(
        "WantedBy=multi-user.target", "WantedBy=default.target"
    )


def breaks_a_unit_line(ch: str) -> bool:
    """Would *ch* start a new line in a unit? A user unit is rewritten line by
    line (``_to_user_unit``), and ``str.splitlines`` also breaks at a vertical
    tab, a form feed, NEL and the Unicode line and paragraph separators, so
    this is ``mods.breaks_a_line``. A tab is only whitespace, in a unit and in
    bash. Every value the server, panel and task units render faces it."""
    return ch != "\t" and breaks_a_line(ch)


class UnitPathError(ServiceUserError):
    """A path zsm writes into a unit holds something systemd cannot take
    there. A ``ServiceUserError``, so every install path that reports one
    already reports this."""


def unit_value(what: str, value: str, unit: str) -> str:
    """*value* as written into ``ExecStart=`` or ``WorkingDirectory=`` of
    *unit*, which systemd parses itself (no shell).

    systemd expands ``%`` specifiers in both, so ``%`` is written ``%%``. A
    double quote, a backslash or anything that starts a new unit line
    (``breaks_a_unit_line``) is refused rather than escaped (plans/68 B10,
    LB-7); the caller reports the refusal. A normal path holds none of them and
    comes back unchanged. The panel's unit and the scheduled tasks' units both
    go through this, so they share one rule.
    """
    if any(c in '"\\' or breaks_a_unit_line(c) for c in value):
        raise UnitPathError(
            f"{what} ({value!r}) holds a double quote, a backslash, a control "
            f"character or a line separator, so it cannot be written into {unit}. "
            "Run zsm from a path without them."
        )
    return value.replace("%", "%%")


def unit_exec_word(what: str, value: str, unit: str) -> str:
    """*value* as the program word of ``ExecStart=``: ``unit_value``, and
    double-quoted only when systemd would otherwise split it (whitespace) or
    read a quote in it (``'``). A path that needs neither is written as it is,
    so an installed unit's text does not change (plans/68 B19)."""
    escaped = unit_value(what, value, unit)
    if any(c.isspace() or c == "'" for c in escaped):
        return f'"{escaped}"'
    return escaped


# --- launch-command safety ------------------------------------------------
#
# The unit's ExecStart runs the server through ``/bin/bash -c '…'`` and the
# Windows start-server.bat runs it through cmd, so every value substituted into
# that one command line is parsed by a shell before DayZServer sees it. A value
# carrying shell/cmd/systemd syntax — a quote, ``$(…)``, ``;``, ``%`` — would be
# interpreted rather than handed to the server. These sets are the characters
# that do that; a normal value (a path, ``-cpuCount=2``, ``@CF``) holds none of
# them, so a normal unit still renders byte for byte as it did. The split is by
# platform because each host renders only its own template: bash after systemd
# unquotes the outer single quotes on Linux, cmd on Windows.
#
# A value zsm wraps in double quotes itself (a mod name inside ``mod_arg``,
# and the ``-config``/``-profiles``/``-BEpath`` arguments, each rendered as one
# quoted word) only has to be kept from breaking out of those quotes, so it
# keeps a looser set: a space or parentheses in a Workshop mod folder name or a
# server path is fine there. The launch flags are several words and stay bare,
# facing the full shell. ``\`` is a path separator on Windows, so it is
# allowed there.
_UNSAFE_BARE_POSIX = frozenset("'\"`$\\;&|<>()%\n\r")
_UNSAFE_QUOTED_POSIX = frozenset("'\"`$\\%\n\r")
_UNSAFE_BARE_WIN = frozenset('%&|<>^"()\n\r')
_UNSAFE_QUOTED_WIN = frozenset('%"\n\r')
_UNSAFE_WORD_START_POSIX = frozenset("#~")


class LaunchCommandError(ValueError):
    """A config value would reach the shell/cmd that starts the server as
    syntax, so the unit / start-server.bat is refused rather than written with
    it. A ``ValueError`` subclass, so the Run Command save maps it to a 400 and
    every existing ``except ValueError`` still catches it. Names the field and
    the offending character.
    """


def _check_launch_value(field: str, value: str, *, quoted: bool) -> None:
    if IS_WINDOWS:
        unsafe = _UNSAFE_QUOTED_WIN if quoted else _UNSAFE_BARE_WIN
    else:
        unsafe = _UNSAFE_QUOTED_POSIX if quoted else _UNSAFE_BARE_POSIX
    for ch in value:
        if ch in unsafe or breaks_a_unit_line(ch):
            shown = {"\n": "a newline", "\r": "a carriage return"}.get(ch, repr(ch))
            raise LaunchCommandError(
                f"{field} contains {shown}, which the server's launch command "
                f"would read as syntax rather than pass to the server. Remove it."
            )
    # bash reads these only at the start of a bare word: "#" turns the rest of
    # the command line, -port= included, into a comment, and "~" becomes the
    # home directory. Inside a word, or in cmd, they are plain characters.
    if not quoted and not IS_WINDOWS:
        for word in value.split():
            if word[0] in _UNSAFE_WORD_START_POSIX:
                raise LaunchCommandError(
                    f"{field} has a word starting with {word[0]!r}, which the server's "
                    f"launch command would read as syntax rather than pass to the "
                    f"server. Remove it."
                )


SERVER_DIR_FIELD = "Server directory"
CONFIG_PATH_FIELD = "Server config path (-config)"
PROFILES_PATH_FIELD = "Profiles directory (-profiles)"
BATTLEYE_PATH_FIELD = "BattlEye directory (-BEpath)"
LAUNCH_FLAGS_FIELD = "Launch flags"

#: The config fields the launch command renders as one double-quoted word.
_QUOTED_LAUNCH_FIELDS = {
    "config_path": CONFIG_PATH_FIELD,
    "profiles_path": PROFILES_PATH_FIELD,
    "battleye_path": BATTLEYE_PATH_FIELD,
}


def _mod_name_field(name: str) -> str:
    return f"Mod name {name!r}"


def launch_field_problem(config: "DayzConfig | None", field: str, value: str) -> str | None:
    """Why `_validate_launch_command` would refuse *value* in config *field*, or None.

    The Run Command save runs that check over the whole config. This is the
    same check for one field, for a value that arrives another way: a bundle
    import (plans/68 PATH-6). A relative profiles or BattlEye path is checked as
    given. Joined onto a server dir that passed its own check, it adds no other
    character. When *config* marks mods server-side, typed ``-serverMod=``
    entries face the mod-name check, because `_get_server_mod_parts` moves them
    out of the flags then. Fields the launch command does not carry answer None.
    """
    try:
        if field == "server_args":
            rest = value
            if config is not None and modlist.server_only(config):
                from app.shared.server_args import split_server_mod_flag

                typed, rest = split_server_mod_flag(value)
                for name in typed:
                    _check_launch_value(_mod_name_field(name), name, quoted=True)
            _check_launch_value(LAUNCH_FLAGS_FIELD, rest, quoted=False)
        elif field in _QUOTED_LAUNCH_FIELDS:
            _check_launch_value(_QUOTED_LAUNCH_FIELDS[field], value, quoted=True)
    except LaunchCommandError as e:
        return str(e)
    return None


def server_dir_problem(value: str) -> str | None:
    """Why *value* cannot be the server dir, or None.

    Every place that stores ``server_dir`` asks this, so a value the unit or
    .bat could never render is refused when it is typed rather than at the
    next install. It is the render's own check (the quoted set), not a copy.
    """
    try:
        _check_launch_value(SERVER_DIR_FIELD, value, quoted=True)
    except LaunchCommandError as e:
        return str(e)
    return None


def mod_name_problem(name: str) -> str | None:
    """Why *name* cannot be one mod in the launch command, or None.

    The render's own check for a ``-mod=`` entry (the quoted set), for a name
    that is decided before any render: one taken from a Workshop title.
    """
    try:
        _check_launch_value(_mod_name_field(name), name, quoted=True)
    except LaunchCommandError as e:
        return str(e)
    return None


def server_port_number(config) -> int:
    """``config.server_port`` as the number the unit renders after ``-port=``.

    A bundle import sets it without the Run Command form's ``int()``, so it
    can be a string; a digit string is the same port and passes. Anything
    else raises ``LaunchCommandError``. Every reader that does arithmetic on
    the port (the firewall's port list) goes through this, so it accepts
    exactly what the unit renders.
    """
    port = str(config.server_port)
    if not (port.isascii() and port.isdigit() and 1 <= int(port) <= 65535):
        raise LaunchCommandError(
            f"Game port (-port) must be a number from 1 to 65535, got {config.server_port!r}."
        )
    return int(port)


def _validate_launch_command(
    config: "DayzConfig", replacements: dict[str, str] | None = None
) -> None:
    """Refuse to render a unit / .bat whose values would reach the shell as
    syntax. Called from both render paths, which every install path goes
    through, so a dangerous value is caught before anything is written; the Run
    Command editor runs the same check on save so the admin is told at once.

    Not part of the web path lock (plans/58) — this is a bug fix for every
    install, whether or not the lock is on.
    """
    # The server dir is a line of its own in the unit (WorkingDirectory=) and sits
    # inside double quotes in the .bat (cd /d "…"), so it faces the quoted set.
    _check_launch_value(SERVER_DIR_FIELD, config.server_dir, quoted=True)
    # The three path arguments are each rendered as one double-quoted word, so
    # they face the quoted set. Checked as rendered: a relative profiles/BattlEye
    # path is joined onto the server dir, and the joined value is what is quoted.
    _check_launch_value(CONFIG_PATH_FIELD, config.config_path, quoted=True)
    r = replacements if replacements is not None else _get_template_replacements(config)
    _check_launch_value(PROFILES_PATH_FIELD, r["profiles_path"], quoted=True)
    _check_launch_value(BATTLEYE_PATH_FIELD, r["battleye_path"], quoted=True)
    if IS_WINDOWS:
        # As rendered, so after _path_arg dropped the trailing backslashes: a
        # drive root (C:\, C: or a bare \) is what that leaves unusable.
        for field, raw, rendered in (
            (CONFIG_PATH_FIELD, config.config_path, r["config_path"]),
            (PROFILES_PATH_FIELD, config.profiles_path, r["profiles_path"]),
            (BATTLEYE_PATH_FIELD, config.battleye_path, r["battleye_path"]),
        ):
            if _DRIVE_ROOT.fullmatch(rendered) or (raw and not rendered):
                raise LaunchCommandError(
                    f"{field} is a drive root, which the server cannot be given as a "
                    "path: with its trailing backslash the argument runs into the next "
                    "one, and without it, it means that drive's current directory. "
                    "Use a folder on the drive instead."
                )
    server_port_number(config)
    server_mod_string, server_args = _get_server_mod_parts(config)
    _check_launch_value(LAUNCH_FLAGS_FIELD, server_args, quoted=False)
    names = _get_mod_string(config).split(";") + server_mod_string.split(";")
    for name in filter(None, names):
        _check_launch_value(_mod_name_field(name), name, quoted=True)


def _render_service(config: "DayzConfig") -> str:
    """Render the systemd service file template with current config values.

    In user scope the rendered system unit is rewritten for the per-user
    manager (drops ``User=``, swaps the install target).
    """
    replacements = _get_template_replacements(config)
    _validate_launch_command(config, replacements)
    text = _render_template(TEMPLATE_PATH, replacements)
    if get_scope(config).user_mode:
        # User units run as their owner; _to_user_unit drops User= entirely, so
        # there is nothing for systemd to resolve and nothing to validate.
        return _to_user_unit(text)
    _validate_unit_user(_current_user())
    return text


def _render_start_bat(config: "DayzConfig") -> str:
    """Render the Windows start-server.bat template."""
    replacements = _get_template_replacements(config)
    _validate_launch_command(config, replacements)
    return _render_template(START_BAT_TEMPLATE_PATH, replacements)


def _render_stop_bat(config: "DayzConfig") -> str:
    """Render the Windows stop-server.bat template."""
    return _render_template(STOP_BAT_TEMPLATE_PATH, _get_template_replacements(config))


def write_windows_scripts(config: "DayzConfig") -> tuple[Path, Path]:
    """Render ``start-server.bat`` and ``stop-server.bat`` into the server
    folder and return their paths. The one writer: the wizard, ``zsm service
    generate`` / ``update``, the panel's regenerate and a start that finds no
    script all save the same two files. Both are rendered before either is
    written, so a refused launch command (``LaunchCommandError``) writes nothing.
    """
    start_content = _render_start_bat(config)
    stop_content = _render_stop_bat(config)
    start_path = Path(config.server_dir) / START_BAT_NAME
    stop_path = Path(config.server_dir) / STOP_BAT_NAME
    start_path.write_text(start_content, encoding="utf-8")
    stop_path.write_text(stop_content, encoding="utf-8")
    return start_path, stop_path


def build_launch_argv(config: "DayzConfig") -> list[str]:
    """The exact ``DayZServer`` launch argv (binary + flags).

    Single source shared by the systemd unit's ``ExecStart``, the Windows
    ``start-server.bat``, ``zsm supervise``, and the web Run Command view,
    so what those surfaces show/run is always identical. Mirrors the token
    order in ``templates/dayz-server.service.j2``.
    """
    r = _get_template_replacements(config)
    return [
        f"./{SERVER_BINARY}",
        f"-config={r['config_path']}",
        *([f"-mod={r['mod_string']}"] if r["mod_string"] else []),
        *([f"-serverMod={r['server_mod_string']}"] if r.get("server_mod_string") else []),
        f"-profiles={r['profiles_path']}",
        f"-BEpath={r['battleye_path']}",
        *shlex.split(r["server_args"]),
        f"-port={r['server_port']}",
    ]


def build_launch_command_info(config: "DayzConfig") -> dict:
    """The generated launch command plus a structured, source-tagged breakdown.

    Returns ``{"command": str, "argv": list[str], "parts": [...]}``. Each part
    carries an ``origin`` string naming *where* that piece is changed
    (``"mods"`` → Mods page; ``"config_path"`` / ``"paths"`` / ``"flags"`` /
    ``"port"`` → Run Command editor). Presentation layers (the web UI) map
    ``origin`` onto their own navigation. Pure — no imports of app.web.
    """
    r = _get_template_replacements(config)
    mods = [m for m in r["mod_string"].split(";") if m]
    server_args = (r["server_args"] or "").strip()

    parts: list[dict] = [
        {"label": "Server binary", "flag": "", "value": f"./{SERVER_BINARY}", "origin": None},
        {"label": "Server config", "flag": "-config", "value": r["config_path"], "origin": "config_path"},
    ]
    if mods:
        parts.append(
            {"label": "Mods", "flag": "-mod", "value": r["mod_string"], "mods": mods, "origin": "mods"}
        )
    server_mods = [m for m in (r.get("server_mod_string") or "").split(";") if m]
    if server_mods:
        parts.append(
            {
                "label": "Server-side mods",
                "flag": "-serverMod",
                "value": r["server_mod_string"],
                "mods": server_mods,
                "origin": "mods",
            }
        )
    parts += [
        {"label": "Profiles dir", "flag": "-profiles", "value": r["profiles_path"], "origin": "paths"},
        {"label": "BattlEye dir", "flag": "-BEpath", "value": r["battleye_path"], "origin": "paths"},
    ]
    if server_args:
        parts.append({"label": "Launch flags", "flag": "", "value": server_args, "origin": "flags"})
    parts.append({"label": "Game port", "flag": "-port", "value": r["server_port"], "origin": "port"})

    argv = build_launch_argv(config)
    # A word holding whitespace is shown in double quotes, as the unit and
    # start-server.bat pass it, so a server path with a space reads as one
    # argument. Every other word is shown bare, so a normal install's preview
    # (and its highlighting) is unchanged.
    command = " ".join(f'"{a}"' if any(c.isspace() for c in a) else a for a in argv)
    return {"command": command, "argv": argv, "parts": parts}


# ---------------------------------------------------------------------------
# Process / service state (pure — no prints)
# ---------------------------------------------------------------------------

def is_server_running(config: "DayzConfig | None" = None) -> bool:
    """Check if the DayZ server is running (cross-platform).

    ``config`` selects the systemd scope; pass the caller's already-loaded
    config on hot paths (web status poll) to avoid re-reading config.json.
    """
    if IS_WINDOWS:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq DayZServer_x64.exe"],
                capture_output=True,
                text=True,
            )
            return "DayZServer_x64.exe" in result.stdout
        except FileNotFoundError:
            return False
    return _unit_is_running(*systemd_unit_state(config))


def _unit_is_running(active: str, sub: str) -> bool:
    """The systemd half of ``is_server_running``, from ActiveState/SubState."""
    if active == "active":
        return True
    if active == "activating":
        # Restart=always parks a crash-looping unit in activating/auto-restart
        # between attempts, where `is-active` still reports "activating" even
        # though no server process exists. Only a real start-up counts.
        return sub != "auto-restart"
    return False


def crash_looping(config: "DayzConfig | None" = None) -> bool:
    """Is the unit between two automatic restarts (activating/auto-restart)?

    ``is_server_running`` says no there, since no server process exists, but
    systemd starts it again in a moment. A stop must still run
    ``systemctl stop`` then, or the loop goes on after "already stopped"."""
    return systemd_unit_state(config) == ("activating", "auto-restart")


# Unit states that `is_server_running` does not call running although the
# server process is still there: a stop in progress, which lasts until the
# process exits (up to TimeoutStopSec), and a reload. The DayZ unit has no
# ExecReload, so `reloading` is here for completeness.
_STILL_UP_STATES = frozenset({"deactivating", "reloading"})


def is_server_process_alive(config: "DayzConfig | None" = None) -> bool:
    """Might the server process still be alive? ``is_server_running``, plus a
    stop that has not finished.

    ``is_server_running`` answers "is the server up", and is False as soon as
    the unit is ``deactivating``. The process is still there until it exits,
    and what it puts back as it exits lands then: BattlEye renames its active
    copy back over beserver_x64.cfg (measured in 2026 Q3), so a write made
    during a slow stop is lost. A guard for "the server would overwrite this"
    asks this instead.

    - systemd: from ``activating`` to the end of ``deactivating``.
      ``activating/auto-restart`` stays False: the process has exited and
      systemd is waiting out ``RestartSec``.
    - Windows: ``is_server_running`` already is this; tasklist lists
      DayZServer_x64.exe until the process exits.
    - Linux without systemd (``zsm supervise``): there is no unit to ask, so
      the process table answers (``is_dayz_process_running``), counting only
      a server started in this instance's server dir.
    """
    if IS_WINDOWS:
        return is_server_running(config)
    if not has_systemd():
        return is_dayz_process_running(config)
    active, sub = systemd_unit_state(config)
    return _unit_is_running(active, sub) or active in _STILL_UP_STATES


_UNIT_PROPERTIES = (
    "ActiveState",
    "SubState",
    # "not-found" when systemd has no such unit, which `systemctl start`
    # answers with exit status 5 (`start_refusal`).
    "LoadState",
    "NRestarts",
    "Result",
    "RestartUSec",
    "InactiveEnterTimestampMonotonic",
    "InactiveEnterTimestamp",
    "ExecMainExitTimestampMonotonic",
)

# systemd prints *USec properties as a formatted time span ("30s", "1min 30s",
# "500ms"), not as a number — only the *Monotonic ones come back raw.
_TIMESPAN_UNITS = (
    ("months", 2629800.0), ("month", 2629800.0),
    ("usec", 0.000001), ("msec", 0.001),
    ("years", 31557600.0), ("year", 31557600.0),
    ("weeks", 604800.0), ("week", 604800.0),
    ("days", 86400.0), ("day", 86400.0),
    ("hours", 3600.0), ("hour", 3600.0),
    ("minutes", 60.0), ("minute", 60.0),
    ("min", 60.0),
    ("sec", 1.0),
    ("ms", 0.001), ("us", 0.000001),
    ("y", 31557600.0), ("w", 604800.0), ("d", 86400.0), ("h", 3600.0),
    ("m", 60.0), ("s", 1.0),
)


def parse_timespan(text: str) -> float | None:
    """systemd's formatted time span → seconds. ``None`` if it isn't one.

    Longest unit first, because "ms" and "min" both start with "m" and a
    naive table would read "1min" as one millisecond — a countdown that is
    60000 times too short.
    """
    text = (text or "").strip().lower()
    if not text or text == "infinity":
        return None
    if text == "0":
        return 0.0
    total = 0.0
    pos = 0
    matched = False
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        start = pos
        while pos < len(text) and (text[pos].isdigit() or text[pos] == "."):
            pos += 1
        if pos == start:
            return None
        try:
            value = float(text[start:pos])
        except ValueError:
            return None
        for name, factor in _TIMESPAN_UNITS:
            if text.startswith(name, pos):
                total += value * factor
                pos += len(name)
                matched = True
                break
        else:
            return None
    return total if matched else None


def unit_runtime_state(config: "DayzConfig | None" = None) -> dict[str, str]:
    """One ``systemctl show`` for everything the panel asks about the unit.

    ``show`` is used over ``is-active`` because the SubState is what separates a
    unit that is genuinely starting (``activating/start``) from one that is
    crash-looping between restarts (``activating/auto-restart``), and because
    ``NRestarts``/``Result`` are how a crash loop becomes something to *say*
    rather than something the admin has to notice.

    Missing keys are simply absent — every caller has to survive an unknown
    unit anyway (no systemd, Windows, unit not installed).
    """
    scope = get_scope(config)
    stdout = scope.query(
        "show", server_unit(config), *(f"--property={p}" for p in _UNIT_PROPERTIES)
    )
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            values[key.strip()] = val.strip()
    return values


def systemd_unit_state(config: "DayzConfig | None" = None) -> tuple[str, str]:
    """``(ActiveState, SubState)`` of the DayZ unit, or ``("", "")`` if unknown."""
    values = unit_runtime_state(config)
    return values.get("ActiveState", ""), values.get("SubState", "")


def restart_stats(config: "DayzConfig | None" = None) -> dict:
    """How the watchdog is behaving, in a shape a page can render.

    The watchdog has always been there (``Restart=always`` on Linux, the
    ``goto watchdog`` loop in the Windows .bat) and has always been invisible:
    a server crashing every thirty seconds looked, from the panel, like a
    server that was merely down. This is the reading half of TODO C6.

    ``{count, looping, limit_hit, result, active_state, sub_state}``:

    * ``count`` — systemd's ``NRestarts``, restarts **since the last manual
      start** (zsm resets it on every start; see `start_server`). So it reads
      as "restarts in this run", not a lifetime total.
    * ``looping`` — parked in ``activating/auto-restart``: crashed, waiting out
      ``RestartSec`` before trying again. This is what `is_server_running`
      already refuses to call running.
    * ``limit_hit`` — systemd gave up. The server is down and **stays** down
      until someone starts it, which is the whole point of the brake and the
      thing that must be said out loud. See `_gave_up` for how that is read.

    ``{}`` on Windows or with no systemd: the .bat watchdog keeps no counter,
    and inventing one from log scraping would be a different feature.
    """
    if IS_WINDOWS or not has_systemd():
        return {}
    values = unit_runtime_state(config)
    if not values:
        return {}
    try:
        count = int(values.get("NRestarts", "0") or 0)
    except ValueError:
        count = 0
    active = values.get("ActiveState", "")
    sub = values.get("SubState", "")
    result = values.get("Result", "")
    looping = active == "activating" and sub == "auto-restart"
    return {
        "count": count,
        "looping": looping,
        "limit_hit": _gave_up(values),
        "result": result,
        "active_state": active,
        "sub_state": sub,
        "seconds_left": _seconds_until_restart(values) if looping else None,
    }


def _gave_up(values: dict[str, str]) -> bool:
    """Did systemd refuse an automatic restart because of the start limit?

    ``Result=start-limit-hit`` is only what a *requested* start gets. When the
    limit stops an **automatic** restart, systemd 255 logs "Start request
    repeated too quickly" and leaves ``Result`` at the crash's own value
    (``signal``) — measured in 2026 Q3 in the test container (plans/42 stage
    7.3), where the brake fired and the panel and doctor both said nothing.

    What does tell it apart is **when** the unit went ``failed``: a refused
    restart happens after waiting out ``RestartSec``, so ``InactiveEnter``
    lands ``RestartUSec`` after the main process exited (measured: 10.03 s
    with ``RestartSec=10``). A plain stop or a crash that is not restarted
    goes ``failed`` at the exit itself (measured: 168 µs). With
    ``RestartSec=0`` the two look alike and only ``start-limit-hit`` counts.
    """
    if values.get("Result", "") == "start-limit-hit":
        return True
    if values.get("ActiveState", "") != "failed":
        return False
    delay = parse_timespan(values.get("RestartUSec", ""))
    try:
        exited_us = int(values.get("ExecMainExitTimestampMonotonic", "0") or 0)
        failed_us = int(values.get("InactiveEnterTimestampMonotonic", "0") or 0)
    except ValueError:
        return False
    if not delay or not exited_us or not failed_us:
        return False
    return (failed_us - exited_us) / 1_000_000 >= delay * 0.9


def _seconds_until_restart(values: dict[str, str]) -> int | None:
    """Seconds until systemd tries again, or ``None`` if it can't be derived.

    **Measured, not assumed** (2026 Q3, a throwaway `Restart=always` /
    `RestartSec=15` unit in the test container, plans/38 B1):
    ``InactiveEnterTimestampMonotonic + RestartUSec`` predicted the moment the
    unit went active again to within ~54 ms. ``ConditionTimestampMonotonic``
    does not move between retries; ``NRestarts`` increments when the restart
    fires, not when the wait starts.

    The base clock is **``time.monotonic()``, not ``/proc/uptime``.** The plan
    said uptime; the measurement says otherwise — on the same box systemd's
    monotonic read 144511 s while ``/proc/uptime`` read 599540 s, because
    uptime counts suspended time (CLOCK_BOOTTIME) and these timestamps are
    CLOCK_MONOTONIC. ``time.monotonic()`` matched systemd's base to within
    0.3 s. Using uptime would have made the countdown days wrong on any laptop
    that has ever slept.
    """
    delay = parse_timespan(values.get("RestartUSec", ""))
    if delay is None:
        return None
    try:
        base_us = int(values.get("InactiveEnterTimestampMonotonic", "0") or 0)
    except ValueError:
        base_us = 0
    if base_us > 0:
        remaining = (base_us / 1_000_000) + delay - time.monotonic()
    else:
        # Monotonic unavailable (some older systemd, some containers). The
        # wall clock is second best: it can step, so a countdown built on it
        # can jump — but a jumpy countdown beats none.
        stamp = _parse_unit_timestamp(values.get("InactiveEnterTimestamp", ""))
        if stamp is None:
            return None
        remaining = stamp + delay - time.time()
    # A wait that has elapsed but not yet fired reads as 0, never negative.
    return max(0, int(remaining + 0.999))


def _parse_unit_timestamp(text: str) -> float | None:
    """``Mon 2026-09-21 16:55:18 UTC`` → epoch seconds, or None."""
    from datetime import datetime, timezone

    text = (text or "").strip()
    if not text:
        return None
    parts = text.split()
    if len(parts) < 4:
        return None
    try:
        stamp = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if parts[3].upper() == "UTC":
        return stamp.replace(tzinfo=timezone.utc).timestamp()
    return stamp.astimezone().timestamp()


def is_dayz_process_running(config: "DayzConfig | None" = None) -> bool:
    """True if a DayZServer process is alive, regardless of how it was started.

    Unlike is_server_running() (systemd-unit-only on Linux), this also catches a
    server launched outside the managed unit: a manual launch, `zsm supervise`
    (Docker/no-systemd), or an orphaned process still holding its UDP sockets
    after a failed `systemctl stop`. Process-table only — does NOT touch the
    filesystem or look for server files.

    Matches on the command line, not the process name: the Enfusion engine
    renames itself to ``enfMain`` once running, so ``pgrep -x DayZServer``
    matches nothing at all while ``/proc/<pid>/cmdline`` still starts with
    ``./DayZServer``. The pattern is anchored to a path/word boundary so a log
    tail or editor holding the string in its argv is not mistaken for a server.

    With *config*, only a server started in that instance's server dir counts
    (plans/44): every launcher runs ``./DayZServer`` from it (the unit's
    ``WorkingDirectory``, ``zsm supervise``'s cwd), so the process's working
    directory says whose it is. A process whose directory cannot be read
    (another user's) still counts; not knowing is not "someone else's".
    """
    if IS_WINDOWS:
        return is_server_running()  # already tasklist/process-based on Windows
    try:
        result = subprocess.run(
            ["pgrep", "-f", DAYZ_PROCESS_PATTERN],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    if config is None:
        return True
    home = config.server_dir_path.resolve()
    for pid in (result.stdout or "").split():
        try:
            if _process_cwd(pid) == home:
                return True
        except FileNotFoundError:
            continue  # exited since pgrep listed it
        except OSError:
            return True
    return False


def _process_cwd(pid: str) -> Path:
    return Path(os.readlink(f"/proc/{pid}/cwd"))


def _kill_watchdog_command() -> list[str]:
    """Build the PowerShell command that finds and kills watchdog cmd.exe processes.

    Matches cmd.exe processes whose CommandLine contains start-server.bat — works
    regardless of window state (visible / minimized / hidden), unlike
    `taskkill /FI "WINDOWTITLE eq ..."` which silently skips non-visible windows.
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{START_BAT_NAME}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'stopped' }"
    )
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]


def _kill_watchdog() -> int:
    """Kill the watchdog cmd.exe processes running start-server.bat. Returns
    how many it ended (0 when that cannot be told)."""
    try:
        result = subprocess.run(_kill_watchdog_command(), capture_output=True, text=True)
    except FileNotFoundError:
        return 0
    return (getattr(result, "stdout", "") or "").count("stopped")


# ---------------------------------------------------------------------------
# Structured (print-free) actions — used by CLI wrappers and app/web
# ---------------------------------------------------------------------------

def systemctl(
    action: str,
    *,
    sudo: bool = True,
    check: bool = True,
    unit: bool = True,
    config: "DayzConfig | None" = None,
    name: str | None = None,
    quiet: bool = False,
) -> ServiceResult:
    """Run a systemctl command in the resolved scope. Inherits stdio —
    `status` output (and any other systemctl-native output) prints directly to
    the terminal; the returned message is only populated on failure. *quiet*
    drops that output, for a best-effort call whose complaint means nothing
    to the admin.

    In user scope this is ``systemctl --user …`` with no sudo regardless of the
    ``sudo`` argument. *name* is the unit acted on when ``unit`` is True — the
    server's (`server_unit(config)`, the instance's own) unless the panel's
    own (``panel_unit``) is meant.
    """
    if not has_systemd():
        return ServiceResult(False, NO_SYSTEMD_MESSAGE)
    scope = get_scope(config)
    args = [action, name or server_unit(config)] if unit else [action]
    cmd = scope.systemctl_argv(*args, sudo=sudo)
    streams = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {}
    try:
        subprocess.run(cmd, check=check, env=scope.subprocess_env(), **streams)
        return ServiceResult(True)
    except subprocess.CalledProcessError as e:
        return ServiceResult(False, f"Failed to {action} service: {e}")
    except FileNotFoundError:
        return ServiceResult(False, "systemctl not found. Is systemd available?")


WATCHDOG_STOPPED = "DayZ Server was not running. Its restart loop (the watchdog window) is stopped."


def windows_stop_server() -> ServiceResult:
    """Stop DayZ server on Windows gracefully, with force kill fallback.

    The watchdog goes first, whether or not the server is up. With the "is it
    running" check ahead of it, a stop did nothing whenever the server was
    down at that moment (a crash loop, or the ten seconds between two
    restarts): it reported success and the watchdog started the server again
    (first-hour test, 2026 Q4).
    """
    watchdogs = _kill_watchdog()
    if not is_server_running():
        return ServiceResult(True, WATCHDOG_STOPPED if watchdogs else "DayZ Server is not running.")

    try:
        subprocess.run(["taskkill", "/IM", "DayZServer_x64.exe"], capture_output=True)
    except FileNotFoundError:
        return ServiceResult(False, "taskkill not found.")

    # Wait up to 120s for the process to exit
    for _ in range(60):
        time.sleep(2)
        if not is_server_running():
            return ServiceResult(True)

    # Last resort: force kill
    try:
        subprocess.run(["taskkill", "/IM", "DayZServer_x64.exe", "/F"], capture_output=True)
        return ServiceResult(True)
    except FileNotFoundError:
        return ServiceResult(False, "taskkill not found.")


def windows_server_status() -> ServiceResult:
    """Raw `tasklist` output for the DayZ server process, or an error message."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq DayZServer_x64.exe"],
            capture_output=True,
            text=True,
        )
        return ServiceResult(True, result.stdout)
    except FileNotFoundError:
        return ServiceResult(False, "tasklist not found.")


def _windows_launch_start_bat(config: "DayzConfig") -> ServiceResult:
    """Launch start-server.bat in a minimized window (shared by start/restart).

    cmd parses the rest of its command line itself, and ``list2cmdline`` quotes
    only an argument holding whitespace, so the server dir never goes on it: the
    script is named by its constant file name and found in ``cwd``.
    """
    bat_path = Path(config.server_dir) / START_BAT_NAME
    if not bat_path.exists():
        # Only the wizard's own install step wrote the scripts, so a server
        # installed from the panel or by `zsm setup` could not be started
        # until `zsm service generate` had been run (first-hour test).
        if not Path(config.server_dir).is_dir():
            return ServiceResult(False, f"No server folder at {config.server_dir}.")
        try:
            write_windows_scripts(config)
        except (ValueError, OSError) as exc:
            return ServiceResult(False, f"Could not write {bat_path}: {exc}")
    subprocess.run(
        ["cmd", "/c", "start", "/min", "", START_BAT_NAME], cwd=str(bat_path.parent), check=False
    )
    return _windows_start_outcome(config)


#: How long a start that cannot work is given to show it. A server with a
#: runtime missing exits while it loads, long before this.
_START_GRACE_SECONDS = 4


def _windows_start_outcome(config: "DayzConfig") -> ServiceResult:
    """Whether the start just launched can work at all.

    With a runtime DLL missing the server exits at once and the watchdog
    restarts it every ten seconds, silently. That is reported, and the loop
    ended, only when both are true: a DLL is missing *and* the server is not
    up after a moment. A server that runs is never refused on the DLL check
    alone, and a machine with every runtime does not wait.
    """
    from app.server.windows_runtime import runtime_problem

    problem = runtime_problem(config)
    if not problem:
        return ServiceResult(True)
    time.sleep(_START_GRACE_SECONDS)
    if is_server_running():
        return ServiceResult(True)
    _kill_watchdog()
    return ServiceResult(False, problem)


# ---------------------------------------------------------------------------
# High-level actions — the app/web entry points (also usable from any CLI code)
# ---------------------------------------------------------------------------

def unit_is_stale(config: "DayzConfig", unit: Path | None = None) -> bool:
    """Does the installed unit differ from what the settings would render?

    A setting that silently does nothing until someone regenerates is worse
    than no setting at all — `Restart=`, `RestartSec=` and the start limit all
    live in a file systemd read at install time, so changing the config
    changes nothing until the unit is rewritten. This is what lets a surface
    say "regenerate" instead of leaving the admin to wonder.

    The comparison is exact rather than clever: `install_service` writes
    `_render_service(config)` byte for byte, so a diff is a real difference.
    False when there is nothing to compare or the render cannot be built (an
    unresolvable `User=`, say) — a claim of staleness has to be earned.

    *unit* defaults to the installed unit. `zsm service install` passes the
    server-dir file it is about to copy, so a unit rendered on another machine
    (a server folder rsynced from a container) is not installed verbatim.
    """
    if IS_WINDOWS:
        return False
    installed = unit or (get_scope(config).unit_dir / server_unit(config))
    if not installed.is_file():
        return False
    try:
        return installed.read_text(encoding="utf-8") != _render_service(config)
    except Exception:
        return False


def service_setup_state(config: "DayzConfig") -> dict:
    """Installed / enabled-at-boot / matches-your-settings state (cross-platform)."""
    if IS_WINDOWS:
        bat_path = Path(config.server_dir) / START_BAT_NAME
        return {"installed": bat_path.exists(), "enabled_at_boot": False, "stale": False}
    scope = get_scope(config)
    installed = (scope.unit_dir / server_unit(config)).exists()
    enabled = False
    if installed and has_systemd():
        enabled = scope.query("is-enabled", server_unit(config)).strip() == "enabled"
    return {
        "installed": installed,
        "enabled_at_boot": enabled,
        "stale": installed and unit_is_stale(config),
    }


def remove_service(
    config: "DayzConfig | None" = None, name: str | None = None
) -> ServiceResult:
    """Stop, disable, and remove the systemd unit *name*, by default the
    server's for *config*'s instance (Linux only)."""
    if IS_WINDOWS:
        return ServiceResult(False, "Service remove is not available on Windows.")
    if not has_systemd():
        return ServiceResult(False, NO_SYSTEMD_MESSAGE)
    scope = get_scope(config)
    name = name or server_unit(config)
    dest = scope.unit_dir / name
    if not dest.exists():
        return ServiceResult(False, "Service is not installed.")
    systemctl("stop", check=False, config=config, name=name)
    systemctl("disable", check=False, config=config, name=name)
    try:
        if scope.user_mode:
            dest.unlink(missing_ok=True)
        else:
            subprocess.run([*sudo_prefix(), "rm", str(dest)], check=True)
    except subprocess.CalledProcessError as e:
        return ServiceResult(False, f"Failed to remove unit file: {e}")
    except OSError as e:
        return ServiceResult(False, f"Failed to remove unit file: {e}")
    except FileNotFoundError:
        return ServiceResult(False, "rm not found.")
    return systemctl("daemon-reload", unit=False, config=config)


def set_boot_enabled(enabled: bool, config: "DayzConfig | None" = None) -> ServiceResult:
    """Enable/disable service start at boot (Linux only)."""
    if IS_WINDOWS:
        return ServiceResult(False, "Boot configuration is not available on Windows.")
    scope = get_scope(config)
    if not (scope.unit_dir / server_unit(config)).exists():
        return ServiceResult(False, "Service is not installed.")
    return systemctl("enable" if enabled else "disable", config=config)


def clear_start_limit(config: "DayzConfig | None" = None) -> None:
    """``systemctl reset-failed``, best effort, before a start we asked for.

    Two things at once, and both are the point of the brake being usable:

    * a unit systemd gave up on (``start-limit-hit``) refuses to start again
      with "start request repeated too quickly" — from the panel that looks
      like the Start button is broken, with the real reason only in the
      journal. Clearing first means a human can always start the server;
    * the start limit counts **every** start, including the ones an admin
      makes. Resetting the counter here keeps the brake aimed at crashes
      rather than at someone restarting three times while fiddling with mods.

    Failure is ignored on purpose: nothing here is worth blocking a start.
    """
    if IS_WINDOWS or not has_systemd():
        return
    # Quiet: before a unit's very first start systemd answers "Failed to reset
    # failed state of unit ...: Unit ... not loaded.", and that line landed in
    # the terminal of `zsm start` and of the panel as if the start had failed.
    systemctl("reset-failed", check=False, config=config, quiet=True)


def restarts_this_run(config: "DayzConfig | None" = None) -> int:
    """systemd's ``NRestarts`` for the unit, 0 when unknown. Read it *before*
    a stop and hand it to `clear_stop_failure`."""
    if IS_WINDOWS or not has_systemd():
        return 0
    try:
        return int(unit_runtime_state(config).get("NRestarts", "0") or 0)
    except ValueError:
        return 0


def clear_stop_failure(config: "DayzConfig | None", restarts_before: int) -> None:
    """``reset-failed`` after a stop Zedstead asked for, so the unit reads
    ``inactive`` and not ``failed``.

    DayZ 1.30 exits 255 on SIGTERM, so every planned stop left the unit
    ``failed``, and the restart task, which
    restarts a failed unit, could not tell it from a server systemd gave up
    on. Skipped when systemd restarted the server in this run
    (*restarts_before*): Stop is not a start, and clearing the counter would
    hide a loop someone stopped to look at (`test_stopping_a_loop_resets_nothing`).
    """
    if IS_WINDOWS or not has_systemd() or restarts_before:
        return
    systemctl("reset-failed", check=False, config=config, quiet=True)


class ServerNotInstalled(RuntimeError):
    """No server dir to write the unit into; the message is
    `server_not_installed`'s sentence."""


def server_not_installed(config: "DayzConfig") -> str | None:
    """The sentence for a server dir that holds no DayZ server, or None.

    `doctor.server_is_installed` decides, as it does for the Doctor row and
    the Dashboard's install card.
    """
    from app.commands.doctor import server_is_installed

    if server_is_installed(config):
        return None
    return (
        f"The DayZ server is not installed in {config.server_dir_path} yet. "
        f"Press Install server on the Dashboard, or run: {zsm_command(config, 'setup')}"
    )


def start_refusal(config: "DayzConfig") -> str | None:
    """Why a start cannot work at all, in a sentence, or None.

    Start on an instance with no server answered "Command '[...]' returned
    non-zero exit status 5". Unlike `runtime_problem`,
    Start anyway does not pass this: with no server binary, or no unit
    systemd knows, a start fails whatever the admin knows. The unit is asked
    of systemd (``LoadState``), never its file, so a unit installed where
    Zedstead does not look still starts, and an unanswered query refuses nothing.
    """
    problem = server_not_installed(config)
    if problem or IS_WINDOWS or not has_systemd():
        return problem
    if unit_runtime_state(config).get("LoadState") != "not-found":
        return None
    return (
        f"The {server_unit(config)} service is not installed. Press Install service on the "
        f"Doctor page, or run {zsm_command(config, 'service update')}, which installs it "
        "and starts the server."
    )


def start_server(config: "DayzConfig", *, anyway: bool = False) -> ServiceResult:
    """Start the DayZ server (cross-platform).

    A start while a runtime the server needs is missing is refused with the
    runtime's sentence, unless *anyway*: the check finds a DLL by its file
    name, so a later DayZ that stops needing one, or a runtime installed
    somewhere the check does not look, must not lock the server out.
    """
    from app.ops.changeset_ops import take_start_snapshot
    from app.server.windows_runtime import runtime_problem

    if not anyway:
        problem = runtime_problem(config)
        if problem:
            return ServiceResult(False, problem)
    take_start_snapshot(config)
    if IS_WINDOWS:
        return _windows_launch_start_bat(config)
    clear_start_limit(config)
    return systemctl("start", config=config)


def stop_server(config: "DayzConfig") -> ServiceResult:
    """Stop the DayZ server (cross-platform)."""
    if IS_WINDOWS:
        return windows_stop_server()
    if not is_server_running(config) and not crash_looping(config):
        return ServiceResult(True, "Service already stopped.")
    restarts = restarts_this_run(config)
    result = systemctl("stop", config=config)
    if result.ok:
        clear_stop_failure(config, restarts)
    return result


def restart_server(config: "DayzConfig") -> ServiceResult:
    """Restart the DayZ server (cross-platform)."""
    from app.ops.changeset_ops import take_start_snapshot

    take_start_snapshot(config)
    if IS_WINDOWS:
        windows_stop_server()
        return _windows_launch_start_bat(config)
    clear_start_limit(config)
    return systemctl("restart", config=config)
