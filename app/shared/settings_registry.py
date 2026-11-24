"""Settings registry — the curated subset of `DayzConfig` fields exposed by
`zsm settings` (CLI) and `/api/settings` (web). Single source of truth for
both surfaces; `app/commands/settings.py` re-imports everything here.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import click

from app.shared.config import DayzConfig


@dataclass(frozen=True)
class _Setting:
    key: str
    section: str
    kind: str  # "str" | "int" | "bool" | "int_list"
    description: str
    # Custom hooks for fields that aren't simple attribute access
    getter: Callable[[DayzConfig], Any] | None = None
    setter: Callable[[DayzConfig, Any], None] | None = None
    # For "str" kind: restrict accepted values to this set (validated in _parse).
    choices: tuple[str, ...] | None = None
    # For "int", and for each item of "int_list": inclusive bounds (_parse).
    # `check` below also gets each item of an "int_list".
    min_value: int | None = None
    max_value: int | None = None
    # What bounds can't say. Gets the parsed value; returns what is wrong with
    # it (the key is prefixed for you), or None when it is fine. A value that
    # breaks something only later (a restart warning that raises, a `zsm web`
    # that can't bind) has to be refused here, on the one write path the CLI
    # and the web share.
    check: Callable[[Any], str | None] | None = None
    # A setting of the panel's listener rather than of one server: only the
    # instance `zsm web` started with can apply it (plans/62 D4). The web
    # shows it read-only on every other instance and refuses a save there.
    panel: bool = False
    # The Configs editor that shows it (app/web/editors/zsm_forms.py), for a
    # value about how Zedstead runs the server; empty = the Settings page, which
    # holds only Zedstead's own behaviour. The CLI lists every setting either way.
    editor: str = ""
    # (text, url) the Settings page shows as a line under the field, where the
    # value comes from, so the reader need not open the tooltip for it.
    link: tuple[str, str] | None = None
    # The name the Settings page shows, when the key humanized says the
    # wrong thing (web_notifier_enabled runs far more than notices). The key
    # stays what config.json and `zsm settings set` use.
    label: str = ""
    # What each of `choices` means ({value: text}): the line under it in the
    # web picker. The description keeps its own wording too (the tooltip, the CLI).
    # Not hashed: a dict cannot be, and _Setting is frozen (hashable) otherwise.
    choice_help: dict[str, str] | None = field(default=None, hash=False)
    # The value's unit ("seconds"), shown in the web box beside the number
    # (webui UnitInput.jsx). The description still says it, for the CLI.
    unit: str = ""


# The notifier's metrics loop clamps to this too, so a hand-edited config.json
# (which never passes through _parse) can't turn it into a busy loop.
METRICS_INTERVAL_MIN = 10

def _absolute_path(value: str) -> str | None:
    """Empty or absolute (``~`` is fine: `DayzConfig` expands it on load). A
    relative path resolves against whatever directory the process started in."""
    if value and not Path(value).expanduser().is_absolute():
        return f"must be an absolute path, got {value!r}"
    return None


def _server_dir(value: str) -> str | None:
    """Absolute, and nothing the launch command would read as syntax."""
    problem = _absolute_path(value)
    if problem:
        return problem
    # Imported here: the settings registry is loaded by every CLI command.
    from app.server.service_control import server_dir_problem

    launch = server_dir_problem(value)
    return f"is refused: {launch}" if launch else None


# The checks that make a setting a local absolute path, which is what puts it
# under the web path lock (web_locked_fields). A check built on _absolute_path
# goes here too, or its setting silently leaves the lock.
_PATH_CHECKS = (_absolute_path, _server_dir)


def _get_manual_load_order(cfg: DayzConfig) -> bool:
    """Kept in the mod list, not in config.json (plans/77)."""
    from app.modmgr import modlist

    return modlist.load(cfg).manual


def _set_manual_load_order(cfg: DayzConfig, on: bool) -> None:
    """Through the Mods page's write path, so switching on keeps the order."""
    from app.modmgr.mods import apply_load_order

    apply_load_order(cfg, manual=on)


def _set_server_branch(cfg: DayzConfig, value: str) -> None:
    """Refused while the server directory holds the other branch's server
    (plans/66 D1): the next update would overwrite one server with the other."""
    from app.shared.steam import branch_refusal

    refusal = branch_refusal(cfg, value)
    if refusal:
        raise click.BadParameter(refusal)
    cfg.server_branch = value


SERVER_BRANCH_HELP = (
    "Which DayZ server this instance installs. 'experimental' is Bohemia's pre-release "
    "build: only players who opt into DayZ Experimental in Steam can join it, it is wiped "
    "often, and mods may not work on it yet. It is a different Steam app, so it needs a "
    "server directory of its own: make it a separate instance."
)
SERVER_BRANCH_CHOICES = {
    "stable": "Bohemia's release build. Every player can join it.",
    "experimental": (
        "Bohemia's pre-release build. Only players who opt into DayZ Experimental in Steam "
        "can join it, it is wiped often, and mods may not work on it yet."
    ),
}

MISSION_CONFLICT_HELP = (
    "When a DayZ update changes a stock mission file that you changed too (db/types.xml, "
    "globals.xml, cfgeventspawns.xml, ...), Zedstead merges the two value by value: DayZ's changes "
    "come in and yours stay. This decides a value you both changed. 'mine' (the default) keeps "
    "yours, 'dayz' keeps DayZ's, and both list it on the Update page. 'ask' keeps DayZ's until you "
    "choose there or with zsm update-conflicts. A file that cannot be merged gets the same "
    "choice for the whole file. Every version is saved in Analysis > Change history, and your mods are "
    "merged back in after every update."
)


_HOST_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def _host(value: str) -> str | None:
    """Empty, an IP address, or a hostname by its syntax. No DNS lookup: the
    name may only resolve where zsm runs, and a save must not wait on DNS.
    Not stripped: a stray space is refused rather than saved."""
    if not value:
        return None
    try:
        ipaddress.ip_address(value)
        return None
    except ValueError:
        pass
    labels = value.rstrip(".").split(".")
    # An all-numeric last label is a mistyped IP ("999.1.1.1"), not a name.
    if (
        len(value) <= 253
        and all(_HOST_LABEL.match(label) for label in labels)
        and not labels[-1].isdigit()
    ):
        return None
    return f"must be an IP address or a host name, got {value!r}"


def host_entries(value: str) -> list[str]:
    """The names in a comma-separated host list as the panel compares them:
    stripped, lowercased, a trailing dot dropped, empties skipped
    (``web_allowed_hosts``; the check below and the web's middleware)."""
    return [e.strip().lower().rstrip(".") for e in (value or "").split(",") if e.strip()]


def _host_list(value: str) -> str | None:
    """Every entry a host name or an IP address. A port or a scheme would
    match no Host header, so it is refused rather than saved as a lockout."""
    for entry in host_entries(value):
        if _host(entry):
            return f"lists {entry!r}, which is not a host name or IP address (leave out any port or scheme)"
    return None


def _restart_template(value: str) -> str | None:
    """The warning is `template.format(minutes=N)` at the moment it is due;
    a template that raises there means players get no warning at all."""
    try:
        value.format(minutes=5)
    except Exception as exc:  # noqa: BLE001 - any failure here is the answer
        return (
            f"can't be used ({type(exc).__name__}: {exc}). Use {{minutes}} for "
            f"the number, and write a literal brace twice ({{{{ or }}}})."
        )
    return None


_DISCORD_HOSTS = ("discord.com", "discordapp.com")


def _discord_webhook(value: str) -> str | None:
    """Empty, or an https:// URL on Discord's own hosts. The sender drops any
    other URL with only a debug log line, so a typo such as http:// turned off
    every Discord notice without a word (browser test 2026-11-17, F4)."""
    from urllib.parse import urlsplit

    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is None or parts.scheme != "https" or not any(
        host == h or host.endswith("." + h) for h in _DISCORD_HOSTS
    ):
        return (
            "must be a Discord webhook URL: https://discord.com/api/webhooks/... "
            "(or empty to turn Discord off)."
        )
    return None


def _restart_seconds(value: int) -> str | None:
    # The unit renderer's own range. Imported here, not at module level:
    # app/shared does not import app/server.
    from app.server.service_control import RESTART_SECONDS_MAX, RESTART_SECONDS_MIN

    if not RESTART_SECONDS_MIN <= value <= RESTART_SECONDS_MAX:
        return f"must be between {RESTART_SECONDS_MIN} and {RESTART_SECONDS_MAX}, got {value}"
    return None


def _off_or_at_least(floor: int) -> Callable[[int], str | None]:
    def _check(value: int) -> str | None:
        if value != 0 and value < floor:
            return f"must be 0 (off) or at least {floor}, got {value}"
        return None
    return _check


def _steam_username(value: str) -> str | None:
    """An anonymous login can't download DayZ (SteamCMD: No subscription), and
    a value SteamCMD would not read as one argument is refused."""
    # Imported here, not at module level: app.shared.steam pulls in requests and bs4.
    from app.shared.steam import steam_username_problem

    problem = steam_username_problem(value)
    return f"is refused: {problem}" if problem else None


def _site_switch(field: str) -> Callable[[DayzConfig, Any], None]:
    """The first turn-on generates the install ID and its key (plans/57 B1)."""
    def _set(c: DayzConfig, value: Any) -> None:
        # Imported here, not at module level: app/shared does not import app/ops.
        from app.ops.site_ops import set_switch

        set_switch(c, field, value)
    return _set


def _attr_get(field: str) -> Callable[[DayzConfig], Any]:
    return lambda c: getattr(c, field)


def _attr_set(field: str) -> Callable[[DayzConfig, Any], None]:
    def _set(c: DayzConfig, value: Any) -> None:
        setattr(c, field, value)
    return _set


_REGISTRY: list[_Setting] = [
    # --- Base ---------------------------------------------------------------
    _Setting("server_dir",      "Base", "str", "Path to the DayZ server directory.", check=_server_dir),
    _Setting("steamcmd_dir",    "Base", "str", "Path to the SteamCMD directory.", check=_absolute_path),
    _Setting(
        "steam_username", "Base", "str",
        "Steam account username for SteamCMD. It must own DayZ; 'anonymous' is refused.",
        check=_steam_username,
    ),
    _Setting(
        "server_branch", "Base", "str", SERVER_BRANCH_HELP,
        choices=tuple(SERVER_BRANCH_CHOICES), choice_help=SERVER_BRANCH_CHOICES,
        setter=_set_server_branch, editor="updates",
    ),
    _Setting(
        "mission_update_conflict", "Base", "str", MISSION_CONFLICT_HELP,
        choices=("ask", "dayz", "mine"),
        choice_help={
            "ask": "Keep DayZ's value until you choose on the Update page or with zsm update-conflicts.",
            "dayz": "Keep DayZ's value.",
            "mine": "Keep your value. The default.",
        },
    ),
    _Setting(
        "service_scope", "Base", "str",
        "systemd service scope: '' = auto (user on a fresh host, system if a "
        "system unit exists or running as root), 'system' = /etc/systemd/system "
        "+ sudo, 'user' = ~/.config/systemd/user with no sudo.",
        choices=("", "system", "user"),
        choice_help={
            "": "User on a fresh host. System if a system unit exists or Zedstead runs as root.",
            "system": "/etc/systemd/system, with sudo.",
            "user": "~/.config/systemd/user, with no sudo.",
        },
    ),

    _Setting(
        "expansion_template_dir", "Base", "str",
        "Unpacked DayZ-Expansion-Missions templates (the folder holding "
        "Chernarus/, Enoch/, …). Empty = <config dir>/expansion-templates, "
        "where the Mods page's Download button unpacks the GitHub zip.",
        check=_absolute_path,
    ),

    # Launch flags (server_args / server_port / cpu_count) are intentionally NOT
    # here — edit them via the web Run Command editor (/configs/runcommand).

    # --- Backups ------------------------------------------------------------
    _Setting("backup_dir",                 "Backups", "str",  "Where backups are written. Empty = default location.", check=_absolute_path),
    _Setting(
        "bundle_export_dir", "Backups", "str",
        "Where exported bundles are written (and imported from). "
        "Empty = <config dir>/bundles.",
        check=_absolute_path,
    ),
    _Setting(
        "backup_max_count", "Backups", "int",
        "Most backups to keep: quick ones per mission, full ones in all. Making one deletes "
        "the oldest past this number, and its log names each. 0 = keep every backup.",
        min_value=0,
    ),
    _Setting(
        "rclone_path", "Backups", "str",
        "Path to an rclone binary for offsite backups. Empty = use one on "
        "$PATH, else download one into the config dir on first use. The "
        "offsite target itself is set on the Backups page (its credentials go "
        "to rclone's own config file, never to config.json).",
    ),
    _Setting("auto_backup_before_restart", "Backups", "bool", "Auto-create a backup before scheduled restarts."),

    # --- Features -----------------------------------------------------------
    _Setting("session_tracker_enabled", "Features", "bool", "Track player join/leave sessions."),
    _Setting("chat_log_enabled",        "Features", "bool", "Tail RCON chat to a log file."),
    _Setting(
        "steam_api_key", "Features", "str",
        "Steam Web API key \u2014 used by the mod update check and Workshop dependency lookups. "
        "Get one at https://steamcommunity.com/dev/apikey.",
        link=("Get one at", "https://steamcommunity.com/dev/apikey"),
    ),
    _Setting(
        "manual_load_order", "Features", "bool",
        "Load mods in the saved order exactly, even where a mod loads before one "
        "it requires (the Mods page's Load order: Manual). Off sorts every mod "
        "after the mods it requires. Switching on keeps the current order. The "
        "Mods page's switch rebuilds the service unit; from here, run "
        "'zsm service generate' afterwards, or the unit keeps the old order.",
        getter=_get_manual_load_order,
        setter=_set_manual_load_order,
    ),
    _Setting(
        "version_check_enabled", "Features", "bool",
        "Ask GitHub whether a newer Zedstead release exists, for the app's update "
        "banner: one HTTPS request to api.github.com, at most once a day, when "
        "the app is opened in a browser. It carries nothing about this server, "
        "but GitHub sees this machine's IP address. Off = no request and no banner.",
        panel=True,
    ),

    # --- Web panel ----------------------------------------------------------
    _Setting(
        "web_notifier_enabled", "Web panel", "bool",
        "Run this server's background tasks in this app: in-game restart "
        "warnings and the join lock, crash detection, doctor checks, metrics, "
        "mod-update checks, log cleanup and the Discord status message, with "
        "their Discord posts and toasts. Off stops all of them, not only their "
        "notices. Turn it off only on a second `zsm web` against the same "
        "config, or every post and warning comes twice. Applies when `zsm web` "
        "restarts.",
        label="Background tasks",
    ),
    _Setting(
        "web_bind_host", "Web panel", "str",
        "Bind host for `zsm web`. Empty = auto: 0.0.0.0 over SSH or in "
        "Docker, served over HTTPS with the self-signed certificate; else "
        "127.0.0.1. Set '0.0.0.0' to always expose (password-gated) when SSH "
        "auto-detect fails (tmux/systemd/sudo); a value set here keeps plain "
        "HTTP unless web_self_signed or a certificate is set.",
        check=_host,
        panel=True,
    ),
    _Setting(
        "web_force_secure_cookie", "Web panel", "bool",
        "Always mark the web session cookie 'Secure' (HTTPS-only). Off by "
        "default: the cookie is marked Secure automatically when the request "
        "is https, including via a trusted reverse proxy. Turn on only if that "
        "detection can't work in your setup — on a plain-HTTP app it makes "
        "login impossible.",
        panel=True,
    ),
    _Setting(
        "web_trusted_proxies", "Web panel", "str",
        "Comma-separated proxy IPs whose X-Forwarded-* headers `zsm web` "
        "trusts. Empty = uvicorn's default, which honours the "
        "FORWARDED_ALLOW_IPS environment variable and otherwise trusts "
        "loopback only — correct for a reverse proxy on the same host. Needed "
        "for correct https detection when TLS is terminated elsewhere. "
        "'*' trusts every client: anyone who can reach the app then sets the "
        "address the access log records and the scheme the session cookie "
        "follows, so use it only on an app nothing but your proxies can reach.",
        panel=True,
    ),
    _Setting(
        "web_allowed_hosts", "Web panel", "str",
        "Comma-separated host names the app answers to, e.g. "
        "'zsm.example.com, localhost'. Empty (the default) answers to any. "
        "It protects an app bound to loopback, behind a reverse proxy or an SSH "
        "tunnel, from DNS rebinding: a web page that points its own name at "
        "127.0.0.1 to reach it. Set, a request whose Host header (port ignored) "
        "is not listed gets 400 and a WebSocket is closed, health checks and "
        "/i/<instance>/ pages included. List the name your proxy passes through, "
        "and localhost or 127.0.0.1, whichever you use on this machine (a tunnel, "
        "a health check). X-Forwarded-Host is not consulted. A typo locks the "
        "browser out: `zsm settings set web_allowed_hosts \"\"` on the server "
        "turns it off at once, with no restart.",
        check=_host_list,
        panel=True,
    ),
    _Setting(
        "web_tls_cert", "Web panel", "str",
        "Absolute path to a PEM certificate for `zsm web` HTTPS. "
        "Both web_tls_cert and web_tls_key must be set; empty = plain HTTP. "
        "`zsm web --self-signed` generates a pair without touching these.",
        check=_absolute_path,
        panel=True,
    ),
    _Setting(
        "web_tls_key", "Web panel", "str",
        "Absolute path to the PEM private key matching web_tls_cert. "
        "Both must be set; setting one alone is a startup error, not a "
        "silent downgrade to HTTP.",
        check=_absolute_path,
        panel=True,
    ),
    _Setting(
        "web_self_signed", "Web panel", "bool",
        "Serve HTTPS with the generated self-signed pair on every `zsm web`, "
        "without --self-signed each time (that flag is per-run: a later bare "
        "`zsm web` drops back to plain HTTP). Better than pointing "
        "web_tls_cert at the generated pair, which then stops renewing. "
        "Cannot be combined with web_tls_cert/web_tls_key.",
        panel=True,
    ),
    _Setting(
        "web_lock_paths", "Web panel", "bool",
        "Lock every path and program setting against changes from the web UI "
        "(server_dir, steamcmd_dir, rclone_path, the launch-command paths, the "
        "TLS cert/key, and any path-bearing launch flag). Off by default. Turn "
        "it on to hand someone a web login on a shared box without letting them "
        "repoint Zedstead at the source or at a program of their choosing. Only the "
        "CLI on the box can turn it back off: `zsm settings set web_lock_paths "
        "false`. A web request may turn it on but never off.",
    ),

    # --- RCon ---------------------------------------------------------------
    # rcon_password / rcon_port are deliberately not here: they live in the
    # server's own beserver_x64.cfg and are edited through Configs -> BattlEye.
    # rcon_host has no counterpart in that file — it is purely which address
    # zsm dials — so this registry is its only home.
    _Setting(
        "rcon_host", "RCon", "str",
        "Address Zedstead connects to for BattlEye RCon. Default 127.0.0.1 = the "
        "server on this machine. Point it at another host to manage players, "
        "broadcast and kick/ban on a server Zedstead does not run — a rented one, "
        "for instance. Everything else (updates, mods, start/stop, the XML "
        "merge) still acts on the local install. Note that BattlEye RCon is "
        "unencrypted UDP: over the internet the password and every command "
        "travel in the clear, so prefer a VPN or an SSH tunnel to the host.",
        check=_host, editor="battleye",
    ),

    # --- Scheduler ------------------------------------------------------------
    _Setting(
        "update_max_players", "Scheduler", "int",
        "Defer the scheduled 'Server + Mod Update' task when more than this "
        "many players are online. 0 = any player online defers it to the next "
        "scheduled time; -1 = never check. Needs RCon configured — without it "
        "Zedstead cannot count players and updates regardless.",
        min_value=-1, editor="updates",
    ),
    _Setting(
        "post_update_boot_timeout", "Scheduler", "int",
        "After an update that starts the server, how many seconds to wait for "
        "it to become joinable before Zedstead reports the boot as failed and "
        "names the mods behind it. A crash is reported at once; this only "
        "matters for a server that hangs while loading.",
        min_value=30, max_value=3600, editor="updates", unit="seconds",
    ),

    # --- Notifications -------------------------------------------------------
    _Setting("toast_player_connect",  "Notifications", "bool", "Notify on player connect/disconnect."),
    _Setting("toast_restart_warning", "Notifications", "bool", "Notify on restart-warning countdowns."),
    _Setting("toast_doctor_check",    "Notifications", "bool", "Notify when a health check starts failing."),
    _Setting("toast_version_update",  "Notifications", "bool", "Notify when a new Zedstead version is available."),

    # --- Monitoring ----------------------------------------------------------
    _Setting(
        "metrics_interval", "Monitoring", "int",
        "Seconds between performance-metric samples (FPS/memory/CPU/players). "
        f"At least {METRICS_INTERVAL_MIN}.",
        min_value=METRICS_INTERVAL_MIN, unit="seconds",
    ),
    _Setting(
        "mod_update_check_interval", "Monitoring", "int",
        "Seconds between workshop mod-update checks (needs a Steam API key). "
        "0 = off; otherwise at least 600.",
        check=_off_or_at_least(600), unit="seconds",
    ),

    # --- Log cleanup ----------------------------------------------------------
    _Setting(
        "log_cleanup_days", "Log Cleanup", "int",
        "Auto-delete server logs older than this many days (0 = disabled).",
        min_value=0, unit="days",
    ),
    _Setting(
        "log_cleanup_keep", "Log Cleanup", "int",
        "Always keep at least this many newest log files per type.",
        min_value=0,
    ),

    # --- Crash recovery ------------------------------------------------------
    # The unit's watchdog is `Restart=always` + `RestartSec=30`, which on its
    # own relaunches a server that cannot start, every 30s, for ever and in
    # silence. These two are the brake; the unit has to be regenerated for a
    # change to take effect.
    _Setting(
        "restart_limit_burst", "Crash Recovery", "int",
        "Give up after this many restarts inside the window below "
        "(0 = never give up, the old behaviour). A start you ask for resets "
        "the count, so this only ever counts crashes.",
        min_value=0, editor="crash-recovery",
    ),
    _Setting(
        "restart_limit_interval_seconds", "Crash Recovery", "int",
        "The window the restarts above are counted in, in seconds.",
        min_value=0, editor="crash-recovery", unit="seconds",
    ),
    _Setting(
        "service_restart_policy", "Crash Recovery", "str",
        "When systemd restarts the server by itself. 'always' is the default; "
        "'on-failure' leaves a clean shutdown alone, so a scheduled restart's "
        "own stop is not undone; 'no' never retries. Regenerate the service "
        "files after changing this — Doctor → Service setup, or "
        "'zsm service install' — or the unit on disk keeps the old value.",
        choices=("always", "on-failure", "no"), editor="crash-recovery",
        choice_help={
            "always": "After every exit. The default.",
            "on-failure": "After a crash only. A clean shutdown, such as a scheduled restart's own stop, is left alone.",
            "no": "Never.",
        },
    ),
    _Setting(
        "service_restart_seconds", "Crash Recovery", "int",
        "Seconds systemd waits before each automatic restart (1-3600). "
        "Regenerate the service files after changing this.",
        check=_restart_seconds, editor="crash-recovery", unit="seconds",
    ),

    # --- Restart warnings ---------------------------------------------------
    _Setting(
        "restart_warning_minutes", "Restart Warnings", "int_list",
        "Comma-separated minute marks for restart warnings (e.g. 30,15,5,1), "
        "each 1-1440.",
        min_value=1,
        max_value=1440, editor="restart-warnings", unit="minutes",
    ),
    _Setting(
        "restart_warning_template", "Restart Warnings", "str",
        "Warning message template (use {minutes}).",
        check=_restart_template, editor="restart-warnings",
    ),
    _Setting(
        "restart_lock_at_minutes", "Restart Warnings", "int",
        "Lock new joins this many minutes before restart, over RCon (0 = off). "
        "Unlocked again if the restart does not happen.",
        min_value=0, editor="restart-warnings", unit="minutes",
    ),

    # --- Discord ------------------------------------------------------------
    _Setting("discord_webhook_url",          "Discord", "str",  "Discord webhook URL for server notifications.",
             check=_discord_webhook),
    _Setting("discord_hook_start",           "Discord", "bool", "Notify when the server starts."),
    _Setting("discord_hook_stop",            "Discord", "bool", "Notify when the server stops."),
    _Setting("discord_hook_crash",           "Discord", "bool", "Notify when the server crashes."),
    _Setting("discord_hook_update",          "Discord", "bool", "Notify after server/mod updates."),
    _Setting("discord_hook_wipe",            "Discord", "bool", "Notify after a persistence wipe."),
    _Setting("discord_hook_mods",            "Discord", "bool", "Notify when mods are installed/removed."),
    _Setting("discord_hook_player_join",     "Discord", "bool", "Notify on player join."),
    _Setting("discord_hook_player_leave",    "Discord", "bool", "Notify on player leave."),
    _Setting("discord_hook_restart_warning", "Discord", "bool", "Send restart-warning notifications to Discord."),
    _Setting("discord_hook_mod_updates",     "Discord", "bool", "Notify when workshop mods publish a new version."),
    _Setting("discord_hook_chat",            "Discord", "bool", "Mirror in-game chat to Discord."),
    _Setting("discord_hook_status",          "Discord", "bool", "Maintain a live server-status message."),

    # --- zedstead.com (TODO F7, plans/57) ---------------------------------------
    # Every field that is sent is named in its switch's description, and
    # `zsm listing status` prints the exact JSON. site_install_id and
    # site_secret are deliberately NOT here: GET /api/settings serves
    # every registry field, and the secret must never reach a browser.
    _Setting(
        "site_ping", "zedstead.com", "bool",
        "Send zedstead.com an anonymous install count, at most once a day: a random "
        "install ID, the Zedstead version, the OS (linux or windows) and whether "
        "Zedstead runs in Docker. Nothing else, and the backend does not store your "
        "IP. Only `zsm web` sends it (an install that never runs it sends "
        "nothing). "
        "`zsm listing status` prints the exact JSON.",
        setter=_site_switch("site_ping"),
    ),
    _Setting(
        "site_listing", "zedstead.com", "bool",
        "Publish this server on zedstead.com's public server list: its IP address, "
        "game port and query port become public. Its name, map and player count "
        "are read from the server itself over Steam's server query, not sent by "
        "Zedstead. Zedstead sends the random install ID, the query port and "
        "site_address, plus a key that proves the listing is yours. Only "
        "`zsm web` sends it (an install that never runs it sends nothing): "
        "every 15 minutes while the server runs, and it removes the listing when "
        "this is turned off. `zsm listing forget` removes it at once.",
        setter=_site_switch("site_listing"),
    ),
    _Setting(
        "site_address", "zedstead.com", "str",
        "Optional host name shown in the server list instead of the bare IP. "
        "zedstead.com refuses one that does not resolve to the IP the listing is "
        "sent from.",
        check=_host,
    ),
]

_BY_KEY: dict[str, _Setting] = {s.key: s for s in _REGISTRY}


# ---------------------------------------------------------------------------
# Web path lock (plans/58) — the one place the locked set is defined, read by
# the enforcement in WebState.mutate_config and by GET /api/settings.
# ---------------------------------------------------------------------------

# DayzConfig path-ish fields (name ends in _dir/_path/_file/_cert/_key) that the
# lock deliberately does NOT cover, each with the reason. The tripwire test
# (tests/test_web_path_lock.py) fails when a new field with such a name is
# neither locked nor listed here, so the decision is never made by omission.
NOT_WEB_LOCKED: dict[str, str] = {
    "backup_remote_path": "a path on the offsite rclone remote, not this "
    "machine — set on the Backups page, and no local file Zedstead reads or runs",
    "steam_api_key": "a credential, not a path or a program",
}


def panel_keys() -> frozenset[str]:
    """Settings only the instance the panel started with can apply."""
    return frozenset(s.key for s in _REGISTRY if s.panel)


def web_locked_fields() -> frozenset[str]:
    """Every DayzConfig field the web path lock protects.

    Derived, not hand-listed, so a new absolute-path setting is locked
    automatically: every registry entry with a check in ``_PATH_CHECKS``, plus
    the program/path fields that have no registry entry or no such check
    (``rclone_path``, ``workshop_content_dir``, the launch-command paths), plus
    ``web_lock_paths`` itself so a web request can never lift the lock. Turning
    the lock *on* from the web is still allowed — see ``mutate_config``.
    """
    from app.shared.server_args import RUNCOMMAND_CONFIG_FIELDS

    fields = {s.key for s in _REGISTRY if s.check in _PATH_CHECKS}
    fields.update(RUNCOMMAND_CONFIG_FIELDS)
    fields.update({"rclone_path", "workshop_content_dir", "web_lock_paths"})
    return frozenset(fields)


def locked_values(config: DayzConfig) -> dict[str, Any]:
    """Snapshot of every value the lock protects, for a before/after compare.

    Each locked field, plus the path-bearing launch flags parsed out of
    ``server_args`` (keyed ``server_args -config`` etc.) — the latter because a
    path smuggled into the launch flags would otherwise slip past a lock that
    only watched the fields.
    """
    from app.shared.server_args import path_flags_in

    values: dict[str, Any] = {f: getattr(config, f, None) for f in web_locked_fields()}
    for prefix, value in path_flags_in(config.server_args).items():
        values[f"server_args {prefix.rstrip('=')}"] = value
    return values


def _lock_display_name(key: str) -> str:
    if key.startswith("server_args "):
        return f"the {key.split(' ', 1)[1]} launch flag"
    return key


def lock_refusal_message(fields: list[str], panel_instance: str = "", own_instance: str = "") -> str:
    """The one refusal string every surface shows. Names the fields and points
    at the CLI, which is the only place the lock trusts. Deliberately does not
    tell the admin to ``zsm settings set <field>`` for fields that have no CLI
    settings key (config_path, the launch flags, …) — lifting the lock is the
    action that always works.

    *panel_instance* names the instance whose lock it is when that is not the
    one being changed: a panel serving several instances is locked by the
    instance it started with (plans/62 D5). *own_instance* is the instance
    whose own lock refused, named with ``--instance`` so the command lifts
    that lock and not the default instance's."""
    names = ", ".join(sorted({_lock_display_name(f) for f in fields}))
    if panel_instance:
        return (
            f"{names} cannot be changed from the web UI while web_lock_paths is on for "
            f"'{panel_instance}', the instance the app runs on. Change it on the server "
            "with the Zedstead CLI, or lift the lock there with "
            f"`zsm --instance {panel_instance} settings set web_lock_paths false`."
        )
    command = (
        f"zsm --instance {own_instance} settings set web_lock_paths false"
        if own_instance
        else "zsm settings set web_lock_paths false"
    )
    return (
        f"{names} cannot be changed from the web UI while web_lock_paths is on. "
        f"Change it on the server with the Zedstead CLI, or lift the lock there with `{command}`."
    )


# ---------------------------------------------------------------------------
# Value parsing / formatting
# ---------------------------------------------------------------------------

def _parse(setting: _Setting, raw: str) -> Any:
    value = _parse_kind(setting, raw)
    for item in value if setting.kind == "int_list" else [value]:
        problem = _out_of_bounds(setting, item)
        if problem is None and setting.check is not None:
            problem = setting.check(item)
        if problem:
            raise click.BadParameter(f"{setting.key} {problem}")
    return value


# The ceiling of an int setting that names none: a signed 32-bit int. With
# none, 99999999999999999999999 was saved, the page showed it as 1e+23 (past
# what a JSON number holds exactly) and could not save that back, and a huge
# metrics_interval kept the notifier asleep that long.
_INT_CEILING = 2**31 - 1


def _out_of_bounds(setting: _Setting, value: int) -> str | None:
    low, high = setting.min_value, setting.max_value
    if high is None and isinstance(value, int) and not isinstance(value, bool):
        high = _INT_CEILING
    if low is not None and high is not None and not low <= value <= high:
        return f"must be between {low} and {high}, got {value}"
    if low is not None and value < low:
        return f"must be at least {low}, got {value}"
    if high is not None and value > high:
        return f"must be at most {high}, got {value}"
    return None


def _parse_kind(setting: _Setting, raw: str) -> Any:
    if setting.kind == "str":
        if setting.choices is not None and raw not in setting.choices:
            allowed = ", ".join(repr(c) for c in setting.choices)
            raise click.BadParameter(
                f"{setting.key} expects one of {allowed}, got {raw!r}"
            )
        return raw
    if setting.kind == "int":
        try:
            return int(raw)
        except ValueError:
            raise click.BadParameter(f"{setting.key} expects an integer, got {raw!r}")
    if setting.kind == "bool":
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise click.BadParameter(f"{setting.key} expects a boolean (true/false), got {raw!r}")
    if setting.kind == "int_list":
        parts = [p.strip() for p in raw.split(",")]
        if raw.strip() and "" in parts:
            # "15,,5" was saved as [15, 5]: a typo is refused, not guessed at.
            raise click.BadParameter(
                f"{setting.key} expects a comma-separated list of integers, got {raw!r}"
            )
        parts = [p for p in parts if p]
        try:
            return [int(p) for p in parts]
        except ValueError:
            raise click.BadParameter(
                f"{setting.key} expects a comma-separated list of integers, got {raw!r}"
            )
    raise AssertionError(f"unhandled kind {setting.kind!r}")


def _format(setting: _Setting, value: Any) -> str:
    if setting.kind == "int_list":
        return ",".join(str(v) for v in value)
    if setting.kind == "bool":
        return "true" if value else "false"
    return str(value)


def _read(setting: _Setting, config: DayzConfig) -> Any:
    if setting.getter:
        return setting.getter(config)
    return getattr(config, setting.key)


def _write(setting: _Setting, config: DayzConfig, value: Any) -> None:
    if setting.setter:
        setting.setter(config, value)
    else:
        setattr(config, setting.key, value)
