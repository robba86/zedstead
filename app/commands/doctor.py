"""zsm doctor — validate server setup health."""

from __future__ import annotations

import socket
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from app.shared.config import DayzConfig
from app.shared.json_utils import NotUtf8Error, read_text_to_rewrite
from app.shared.platform import IS_DOCKER, IS_WINDOWS
from app.xml.xml_text import friendly_xml_error
from app.xml.xml_utils import load_xml

# No emoji codes: a detail is printed as markup, and Rich turned the `:AB:`
# in a certificate fingerprint into 🆎, so the CLI showed a fingerprint that
# matched nothing. The ✅ and ⚠︎ are literal.
console = Console(emoji=False)

# (label, ok, detail)
CheckResult = tuple[str, bool, str]

# Sections are a label -> section mapping rather than a fourth tuple element:
# CheckResult is unpacked in four places (get_failed_checks, doctor_cmd,
# doctor_report.build_doctor_report and their tests) and widening it would
# break every one. Anything not listed here is a Server check. See plans/23.
SECTION_SERVER = "Server"
SECTION_VPS = "VPS readiness"

CHECK_SECTIONS: dict[str, str] = {
    "CPU": SECTION_VPS,
    "CPU steal": SECTION_VPS,
    "RAM": SECTION_VPS,
    "Swap": SECTION_VPS,
    "Disk space": SECTION_VPS,
    "File handles": SECTION_VPS,
    "Clock": SECTION_VPS,
    "Host": SECTION_VPS,
    "Service scope": SECTION_VPS,
}


def section_for(label: str) -> str:
    """Which doctor section a check belongs to. Unknown labels are Server."""
    return CHECK_SECTIONS.get(label, SECTION_SERVER)


def _vps_checks(config: Optional[DayzConfig]) -> list[CheckResult]:
    """VPS-readiness probes as CheckResults. Linux only; [] on Windows."""
    from app.ops.vps_readiness import probes

    return [(p.label, p.ok, p.detail) for p in probes(config)]


def _check_config() -> CheckResult:
    """Config file exists and loads without error."""
    try:
        config = DayzConfig.load()
        return "Apps config file", True, str(config.paths.config_file)
    except FileNotFoundError:
        return "Apps config file", False, "Not found — run 'zsm init'"
    except Exception as e:
        return "Apps config file", False, f"Load error: {e}"


def server_is_installed(config: DayzConfig) -> bool:
    """True when the DayZ server binary is present in the server dir.

    The gate for every check that can only read files the installer puts there
    (mission, mission XML): without it they report their own "not found" on top
    of the Server-directory row, which is one real finding told three ways.
    """
    binary = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"
    return (config.server_dir_path / binary).exists()


def _check_server_dir(config: DayzConfig) -> CheckResult:
    """Server dir exists and contains the DayZ server binary."""
    server_dir = config.server_dir_path
    if not server_dir.is_dir():
        return "Server directory", False, f"Missing: {server_dir}"
    binary = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"
    if not server_is_installed(config):
        return "Server directory", False, f"Found dir but missing {binary}"
    return "Server directory", True, str(server_dir)


def _check_server_volume(config: DayzConfig) -> CheckResult | None:
    """In a container, a server dir on no volume is deleted with the
    container at the next recreate (night-srv-03). None when it is kept, and
    always outside a container. Its own label, so the "Server directory"
    row's Install button never answers it."""
    from app.shared.container import server_dir_volume_problem

    problem = server_dir_volume_problem(config.server_dir)
    return ("Server volume", False, problem) if problem else None


def _check_server_branch(config: DayzConfig) -> CheckResult:
    """Which DayZ server the directory holds, and whether it is the one this
    instance installs (plans/66). Reads two files and spawns nothing, so it is
    safe in the notifier's loop."""
    from app.shared.steam import branch_mismatch, server_branch

    mismatch = branch_mismatch(config)
    if mismatch:
        return "Server branch", False, mismatch
    if server_branch(config) == "experimental":
        return "Server branch", True, "Experimental (only Experimental clients can join)"
    return "Server branch", True, "Stable"


def _check_steamcmd(config: DayzConfig) -> CheckResult:
    """SteamCMD path exists and is executable."""
    steamcmd = config.steamcmd_path
    if not steamcmd.exists():
        return "SteamCMD", False, f"Not found: {steamcmd}"
    if not IS_WINDOWS and not steamcmd.stat().st_mode & 0o111:
        return "SteamCMD", False, f"Not executable: {steamcmd}"
    return "SteamCMD", True, str(steamcmd)


def _check_steam_login(config: DayzConfig) -> CheckResult:
    """Is SteamCMD's cached login still good?

    The one thing `_check_steamcmd` cannot tell you, and the reason a mod
    download can fail on a host where every other row is green: the token
    SteamCMD caches expires, and an expired one makes workshop downloads exit
    **0** having fetched nothing (see the download guards in the root
    CLAUDE.md). So "SteamCMD is installed" and "SteamCMD can log in" are
    different questions and this asks the second.

    Only reached with ``probe_steam=True``, because asking costs a SteamCMD
    process — see `_run_all_checks`.
    """
    from app.shared.steam import check_steam_login
    from app.shared.steamcmd_lock import SteamcmdBusy

    if not config.steam_username:
        return "Steam login", False, "No Steam username configured — run 'zsm steam-login'"
    if not config.steamcmd_path.is_file():
        return "Steam login", False, f"SteamCMD not installed at {config.steamcmd_path}"
    try:
        ok = check_steam_login(config, wait=0)
    except SteamcmdBusy as exc:
        # Asking would put a second SteamCMD on the same STEAMROOT.
        return "Steam login", True, f"Not checked: {exc}"
    if ok:
        return "Steam login", True, f"Cached login works ({config.steam_username})"
    return (
        "Steam login",
        False,
        f"No usable cached login for {config.steam_username} — run 'zsm steam-login'",
    )


def _check_windows_runtimes(config: DayzConfig) -> CheckResult | None:
    """The Windows half of the OS dependencies row: the Visual C++ and DirectX
    runtimes DayZServer_x64.exe needs. A fresh Windows 11 has neither, the
    server then exits the moment it starts, and doctor used to print fifteen
    OK rows for it (first-hour test, 2026 Q4). None before the server is
    installed, as on Linux: the Visual C++ installer comes with the server.
    """
    if not server_is_installed(config):
        return None
    from app.server.windows_runtime import RUNTIMES, runtime_problem

    problem = runtime_problem(config)
    if problem:
        return "OS dependencies", False, problem
    found = ", ".join(runtime.dll for runtime in RUNTIMES)
    return "OS dependencies", True, f"Visual C++ and DirectX runtimes found ({found})"


def _check_os_deps(config: DayzConfig) -> CheckResult | None:
    """Missing shared libraries for the installed DayZServer binary, and any
    known-missing apt packages. Linux only; None when the binary is absent
    (nothing installed yet is not a finding).

    `ldd` against the real binary is the durable signal — a hardcoded package
    list goes stale when Bohemia changes the runtime — so it is checked first
    and the package list only backs it up. See plans/21.
    """
    if IS_WINDOWS:
        return _check_windows_runtimes(config)

    from app.server.service_control import SERVER_BINARY
    from app.shared.os_packages import (
        SO_TO_PACKAGE,
        effective_utf8_locale,
        missing_packages,
        missing_shared_libs,
        runtime_packages,
    )

    server_dir = config.server_dir_path
    binary = None
    for name in (SERVER_BINARY, f"{SERVER_BINARY}_x64"):
        candidate = server_dir / name
        if candidate.exists():
            binary = candidate
            break
    if binary is None:
        return None

    libs = missing_shared_libs(binary)
    if libs:
        parts = []
        for soname in libs:
            pkg = SO_TO_PACKAGE.get(soname)
            if pkg:
                parts.append(f"{soname} ({pkg}) not found — run: sudo apt-get install {pkg}")
            else:
                parts.append(f"{soname} not found")
        return "OS dependencies", False, "; ".join(parts)

    pkgs = missing_packages(runtime_packages())
    if pkgs:
        return (
            "OS dependencies",
            False,
            f"Missing packages: {' '.join(pkgs)} — run: sudo apt-get install {' '.join(pkgs)}",
        )

    # Locale is reported, never asserted (plan 21 decision 4). Whether DayZ
    # needs en_US.UTF-8 specifically or merely *a* UTF-8 locale cannot be
    # settled without server files, so this must not turn into a check that
    # fails — the Dockerfile generates en_US.UTF-8, and saying which one is in
    # effect is enough for someone comparing a container to a bare box.
    locale = effective_utf8_locale()
    suffix = f"locale {locale}" if locale else "no UTF-8 locale found (C/POSIX only)"
    return "OS dependencies", True, f"all runtime libraries resolve; {suffix}"


def _check_tls_cert(config: DayzConfig) -> CheckResult | None:
    """Expiry of the panel's TLS certificate. ``None`` on most installs.

    Two failures this exists for, both silent by construction:

    * ``ensure_self_signed`` only runs at **startup**, so a panel left running
      past the 825-day validity serves an expired certificate indefinitely —
      nothing re-checks.
    * When it does renew, the banner is word-for-word identical to first run.
      Someone under tmux or nohup gets a browser warning ~795 days later with
      no explanation, and the shape of that is exactly a MITM — which is the
      one signal self-signed depends on.

    Returns ``None`` when no TLS is configured *and* no generated certificate
    exists, which is the default install; there is nothing to say there.
    """
    from app.web import tls as tls_mod

    configured = (getattr(config, "web_tls_cert", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        source = "web_tls_cert"
        if not path.exists():
            return (
                "App certificate",
                False,
                f"{source} points at a missing file: {path} — `zsm web` will "
                "refuse to start (it does not fall back to HTTP).",
            )
    else:
        path = tls_mod.cert_paths()[0]
        source = "web_self_signed" if getattr(config, "web_self_signed", False) else "self-signed"
        if not path.exists():
            return None

    expiry = tls_mod.cert_expiry(path)
    if expiry is None:
        return (
            "App certificate",
            False,
            f"{path} is not a readable PEM certificate ({source}).",
        )

    import datetime

    left = (expiry - datetime.datetime.now(datetime.timezone.utc)).days
    fingerprint = tls_mod.fingerprint_sha256(path)
    when = expiry.date().isoformat()

    if source == "web_self_signed":
        # This one renews itself: every `zsm web` calls ensure_self_signed.
        fix = (
            "Restarting `zsm web` regenerates it — your browser will then ask "
            "you to accept the new one."
        )
    elif source == "self-signed":
        fix = (
            "Restart `zsm web --self-signed` to regenerate it — your browser "
            "will then ask you to accept the new one."
        )
    elif path == tls_mod.cert_paths()[0]:
        # web_tls_cert pinned to the generated pair. A bare `zsm web` takes the
        # config path and never calls ensure_self_signed, so this one does NOT
        # renew itself — which is what web_self_signed exists to avoid.
        fix = (
            "web_tls_cert points at the generated pair, which never renews on "
            "its own. Use `zsm settings set web_self_signed true` instead "
            "(clear web_tls_cert/web_tls_key first)."
        )
    else:
        fix = "Renew it however you issued it, then restart `zsm web`."

    if left < 0:
        return (
            "App certificate",
            False,
            f"Expired on {when} ({source}). A running app never re-checks, so "
            f"it is still serving this one. {fix} SHA-256 {fingerprint}",
        )
    if left < tls_mod.RENEW_WHEN_DAYS_LEFT:
        return (
            "App certificate",
            False,
            f"Expires in {left} day(s), on {when} ({source}). {fix} "
            f"SHA-256 {fingerprint}",
        )
    return (
        "App certificate",
        True,
        f"{source}, expires {when}, SHA-256 {fingerprint}",
    )


def _utf8_problem(path) -> str | None:
    """Why zsm does not rewrite *path*, or None: a file saved in another
    encoding is read with replacement characters and never written back."""
    try:
        read_text_to_rewrite(path)
    except NotUtf8Error as e:
        return str(e)
    except OSError:
        return None
    return None


MOD_LIST_LABEL = "Mod list"


def _check_mod_list(config: DayzConfig) -> CheckResult:
    """The mod list can be read; how many mods it holds (plans/77)."""
    from app.modmgr import modlist

    try:
        names = modlist.installed(config)
    except modlist.ModListError as e:
        return MOD_LIST_LABEL, False, str(e)
    if not names:
        return MOD_LIST_LABEL, True, "No mods installed (vanilla)"
    return MOD_LIST_LABEL, True, f"{len(names)} mod(s) listed"


def _check_mission(config: DayzConfig) -> CheckResult:
    """Mission folder (from serverDZ.cfg) exists on disk."""
    try:
        from app.modmgr.mods import get_current_mission
        problem = _utf8_problem(config.server_cfg_path)
        if problem:
            return "Mission folder", False, problem
        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            return "Mission folder", False, "Could not read mission from serverDZ.cfg"
        mission_dir = config.server_dir_path / "mpmissions" / mission
        if not mission_dir.is_dir():
            return "Mission folder", False, f"Not found: {mission_dir}"
        return "Mission folder", True, str(mission)
    except Exception as e:
        return "Mission folder", False, str(e)


FPS_LOGGING_LABEL = "FPS logging"


def _check_fps_logging(config: DayzConfig) -> CheckResult | None:
    """Whether the server logs its FPS, which the dashboard's FPS history reads.

    DayZ writes "Average server FPS" into the .RPT only with ``logAverageFps``
    (an interval in seconds) in serverDZ.cfg **and** ``-doLogs`` on the command
    line (``dayz-docs/wiki/pages/server-configuration.wiki:79``). Without both,
    every metrics sample has no FPS and the history stays empty (test pass 1,
    F52: 5428 samples, not one FPS value). None when there is no serverDZ.cfg:
    other checks report that.
    """
    from app.parsers.servercfg import read_cfg_values
    from app.shared.server_args import parse_server_args

    cfg = config.server_cfg_path
    if not cfg.is_file():
        return None
    raw = (read_cfg_values(cfg, ["logAverageFps"]).get("logAverageFps") or "").strip()
    try:
        seconds = int(raw)
    except ValueError:
        seconds = 0
    do_logs = bool(parse_server_args(config.server_args or "").get("doLogs"))

    if seconds > 0 and do_logs:
        return FPS_LOGGING_LABEL, True, f"Every {seconds}s"
    if seconds > 0:
        # Asked for and not working: an interval is set and the line is never
        # written. That is a mistake, so it warns.
        return (
            FPS_LOGGING_LABEL,
            False,
            "-doLogs is not in the Run command, so the server logs no FPS and the FPS history stays empty",
        )
    # Off is a choice, and it is how every server starts: Bohemia's stock
    # serverDZ.cfg has no logAverageFps. As a warning this row made `zsm
    # doctor` exit 1 on every new install (first-hour test, 2026 Q4), so it
    # passes and says how to turn the logging on.
    missing = ["logAverageFps is not set in serverDZ.cfg"]
    todo = ["set logAverageFps (seconds between log lines, for example 60)"]
    if not do_logs:
        missing.append("-doLogs is not in the Run command")
        todo.append("add -doLogs")
    return (
        FPS_LOGGING_LABEL,
        True,
        "Off: " + " and ".join(missing) + ", so the server logs no FPS and the FPS history stays empty. "
        "To turn it on, " + " and ".join(todo),
    )


# Serialize the UDP probe so zsm's own concurrent doctor loops (the web
# notifier every 60s, manual doctor, /api/doctor, run-pipeline validation) never
# race each other's bind() and mislabel the collision as a foreign process.
_PORT_PROBE_LOCK = threading.Lock()


def _udp_port_bindable(port: int, attempts: int = 3, delay: float = 0.05) -> bool:
    """True if we can bind the UDP port. Retries to ride out a transient
    collision with a concurrent in-process probe; a real, persistent holder
    fails every attempt."""
    for i in range(attempts):
        with _PORT_PROBE_LOCK:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(1)
                    s.bind(("0.0.0.0", port))
                return True
            except OSError:
                pass
        if i < attempts - 1:
            time.sleep(delay)
    return False


def _check_port(config: DayzConfig) -> CheckResult:
    """Server game port is not held by a foreign (non-DayZ) process."""
    from app.server.service_control import is_dayz_process_running, is_server_running

    port = config.server_port
    # Another instance's port is taken even while that server is stopped and
    # the socket test passes (Adopt said "Port 2402 is free" for exp's).
    from app.server.instances import port_clash

    clash = port_clash(config)
    if clash:
        return "Server port", False, (
            f"{clash.capitalize()}: two servers on this machine cannot share ports. "
            "Give this instance other ports in Configs → Run Command."
        )
    if _udp_port_bindable(port):
        return "Server port", True, f"Port {port} is free"

    # bind() failed on every attempt -> a live process holds the UDP port.
    # UDP releases the port the instant its owner dies, so this is a *current*
    # holder. Decide whether it is our own server before blaming a stranger.
    if is_server_running(config):
        return "Server port", True, f"Port {port} in use by DayZ server"
    if is_dayz_process_running(config):
        return (
            "Server port",
            True,
            f"Port {port} in use by a DayZ server process (not managed by systemd)",
        )
    if is_dayz_process_running():
        return (
            "Server port",
            False,
            f"Port {port} in use by a DayZ server started from another directory "
            "(another instance's?) — see `zsm instances list`",
        )
    return (
        "Server port",
        False,
        f"Port {port} in use by another (non-DayZ) process",
    )


def _rcon_reachable_note(config: DayzConfig) -> str:
    """A line for the Firewall row when RCon is reachable with the game ports
    (plans/68 NET-14): BattlEye binds it to every interface unless ``RConIP``
    says otherwise (measured, app/ops/CLAUDE.md). Text only; it never changes
    the row's verdict, and zsm does not write ``RConIP`` itself."""
    from app.rcon.rcon_ops import local_rcon_bind

    if local_rcon_bind(config) != "":
        return ""
    return (
        f"\n  RCon port {config.rcon_port} is reachable too: RConIP is not set in "
        f"{config.beserver_cfg_path.name}, so the server binds RCon to every interface.\n"
        "  To keep it on this machine: stop the server, set RConIP 127.0.0.1 in "
        "the BattlEye tab of the web UI, then start it."
    )


# What reads the firewall's own state. `zsm service firewall --dry-run` was
# named here and cannot: without a password-free sudo it answers "unknown
# (needs root to read)" as well (first-hour test, 2026 Q4).
_FIREWALL_READ_COMMAND = {
    "ufw": "sudo ufw status verbose",
    "firewalld": "sudo firewall-cmd --list-all",
}


def _check_firewall(config: DayzConfig) -> CheckResult | None:
    """Managed-firewall state vs the ports the server needs.

    Linux only. Never fails on "I could not tell": `ufw status` needs root,
    and a refused read is not a closed port. See plans/22.

    **No backend installed is a WARN, not a skipped row.** This used to return
    None there, on the reasoning that most VPS images ship no firewall and a
    failure row would train users to ignore doctor. A VPS test
    showed what that costs: on a stock Rocky VPS image — public IP, no ufw,
    no firewalld, nothing filtering — doctor printed no Firewall row at all,
    while the Ubuntu box next to it with an *inactive* ufw got a green
    "all ports reachable". The most exposed box was the one told least. It is
    still not a hard failure; it says what is true and how to fix it.
    """
    if IS_WINDOWS:
        return None

    from app.ops.firewall_ops import (
        detect_backend,
        firewall_status,
        missing_ports,
        no_backend_advice,
        NO_BACKEND_SUMMARY,
        required_ports,
    )

    backend = detect_backend()
    if backend is None:
        # In a container the host owns the firewall and there is nothing here
        # for the operator to act on, so the row would be pure noise.
        if IS_DOCKER:
            return None
        return (
            "Firewall",
            False,
            f"{NO_BACKEND_SUMMARY}\n  {no_backend_advice()}" + _rcon_reachable_note(config),
        )

    status = firewall_status(config)
    # Ambiguity first: `zone_conflict` means the table was read *fine* and the
    # answer is merely uncertain, so the "needs root" line below would be wrong
    # twice over. It sets `error` too, which is what keeps every decision path
    # conservative — only the wording differs here.
    if status.zone_conflict:
        return (
            "Firewall",
            True,
            f"{backend} active, but {status.zone_conflict}.\n"
            "  Check which zone holds the server's interface: "
            "firewall-cmd --get-active-zones",
        )
    if status.error or status.active is None:
        return (
            "Firewall",
            True,
            f"could not read {backend} status (needs root) — "
            f"run: {_FIREWALL_READ_COMMAND[backend]}",
        )
    if not status.active:
        return (
            "Firewall",
            True,
            f"{backend} installed but inactive — all ports reachable. "
            "`zsm service firewall --dry-run` shows what would be locked down."
            + _rcon_reachable_note(config),
        )

    rcon_note = _rcon_reachable_note(config) if config.rcon_port in status.open_udp else ""
    missing = missing_ports(config, status)
    if missing:
        listed = ", ".join(f"{spec.port}/udp ({spec.label})" for spec in missing)
        return (
            "Firewall",
            False,
            f"{backend} active but not allowing: {listed}\n"
            "  Fix with: zsm service firewall --apply  (or allow them manually)" + rcon_note,
        )
    allowed = ", ".join(f"{spec.port}/udp" for spec in required_ports(config))
    return "Firewall", True, f"{backend} active — allowing {allowed}" + rcon_note


def _check_service(config: DayzConfig) -> CheckResult:
    """Service installed + boot status (Linux systemd / Windows bat)."""
    if IS_WINDOWS:
        from app.commands.service import START_BAT_NAME, STOP_BAT_NAME
        server_dir = config.server_dir_path
        start_bat = server_dir / START_BAT_NAME
        stop_bat = server_dir / STOP_BAT_NAME
        if start_bat.exists() and stop_bat.exists():
            return "Service", True, "Start/stop scripts installed"
        missing = [n for n, p in [(START_BAT_NAME, start_bat), (STOP_BAT_NAME, stop_bat)] if not p.exists()]
        if START_BAT_NAME in missing:
            # Not a failure: a start writes both scripts when there is none.
            from app.shared.systemd_scope import zsm_command

            return "Service", True, (
                f"Not written yet: {', '.join(missing)}. The first start writes them; "
                f"to write them now: {zsm_command(config, 'service generate')}"
            )
        return "Service", False, f"Missing: {', '.join(missing)}"

    from app.shared.systemd_scope import get_scope, server_unit

    scope = get_scope(config)
    unit = server_unit(config)
    service_path = scope.unit_dir / unit
    if not service_path.exists():
        return "Service", False, f"Not installed: {service_path}"
    from app.server.unit_guard import is_zsm_unit

    try:
        foreign = not is_zsm_unit(service_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        foreign = False  # unreadable: say nothing about who wrote it
    if foreign:
        # A unit under zsm's name that zsm did not write (an adopted server,
        # plans/78 step 9). The unit guard leaves it in place, so zsm's mod
        # list never reaches its launch command; the same line as adopt's.
        from app.shared.systemd_scope import zsm_command

        return "Service", True, (
            f"Installed ({scope.name}), your own unit, not Zedstead's: what you change in Zedstead's "
            "mod list does not reach it until Zedstead's unit replaces it: "
            f"{zsm_command(config, 'service install')} --replace "
            "(copy yours first if you want to keep it)."
        )
    state = scope.query("is-enabled", unit).strip()
    if not state:
        return "Service", True, str(service_path)
    if state == "enabled":
        return "Service", True, f"Installed ({scope.name}) — enabled at boot"
    from app.shared.systemd_scope import zsm_command

    return (
        "Service",
        True,
        f"Installed ({scope.name}) — not enabled at boot. "
        f"Start it at boot: {zsm_command(config, 'service enable')}",
    )


def _check_panel_service(config: DayzConfig) -> CheckResult | None:
    """The panel's own unit (TODO A9), beside the server's. Optional, so not
    having one is not a warning; one systemd has marked failed is."""
    from app.server.panel_unit import panel_commands, panel_state

    state = panel_state(config)
    if not state:
        return None
    if not state["installed"]:
        return (
            "App service",
            True,
            "Not installed — `zsm web` runs only while the shell that started it "
            "does. Keep it running: `zsm service web install`",
        )
    from app.shared.systemd_scope import get_scope

    scope = get_scope(config).name
    if state["active"] == "failed":
        return "App service", False, f"Installed ({scope}) — failed. Logs: {panel_commands(config)[1]}"
    running = "running" if state["active"] == "active" else (state["active"] or "state unknown")
    boot = "enabled at boot" if state["enabled"] else "not enabled at boot"
    return "App service", True, f"Installed ({scope}) — {running}, {boot}"


def _check_crash_loop(config: DayzConfig) -> CheckResult | None:
    """Is the watchdog covering for a server that cannot start?

    A crash loop is the failure that hides best: `Restart=always` puts the
    server back every 30s, so the panel and the player both see "down", and
    nothing anywhere says the unit has now done that nineteen times. ``None``
    when there is nothing to report, so a healthy install gains no row.
    """
    from app.server.service_control import restart_stats

    stats = restart_stats(config)
    if not stats:
        return None
    if stats["limit_hit"]:
        return (
            "Crash recovery",
            False,
            "The server kept crashing and systemd has stopped restarting it — "
            "it stays down until started. Check the .RPT for the crash first.",
        )
    if stats["looping"]:
        return (
            "Crash recovery",
            False,
            f"Crash-looping: {stats['count']} restart(s) since the last start, "
            "waiting to try again.",
        )
    if stats["count"]:
        return (
            "Crash recovery",
            True,
            f"{stats['count']} automatic restart(s) since the last start",
        )
    return None


def _check_service_scope(config: DayzConfig) -> CheckResult | None:
    """Report the resolved systemd scope; in user scope verify linger + bus and
    warn on a dual (system + user) install. Linux/systemd only."""
    if IS_WINDOWS:
        return None
    from app.shared.platform import has_systemd
    if not has_systemd():
        return None

    from app.shared.systemd_scope import (
        SYSTEM_UNIT_DIR,
        _current_user,
        get_scope,
        linger_enabled,
        server_unit,
        user_bus_available,
    )

    scope = get_scope(config)
    if not scope.user_mode:
        return "Service scope", True, "system scope (sudo systemctl)"

    user = _current_user()
    # Dual install: both a system unit and the user unit exist → ambiguous.
    unit = server_unit(config)
    if (SYSTEM_UNIT_DIR / unit).exists() and (scope.unit_dir / unit).exists():
        return (
            "Service scope",
            False,
            "Both a system and a user unit exist — run `zsm service migrate-scope user` "
            "to remove the old system unit.",
        )
    if not linger_enabled(user):
        return (
            "Service scope",
            False,
            f"user scope, but lingering is disabled — services won't start at boot. "
            f"Run `sudo loginctl enable-linger {user}` or `zsm service enable-linger`.",
        )
    if not user_bus_available():
        return (
            "Service scope",
            False,
            "user scope, but the per-user systemd bus is unreachable. "
            f"Run `sudo loginctl enable-linger {user}`, then re-check.",
        )
    return "Service scope", True, "user scope — lingering enabled, bus reachable"


def _check_server_query(config: DayzConfig) -> CheckResult:
    """Check if the server responds to Steam A2S queries (visible in browser)."""
    from app.commands.service import is_server_running

    if not is_server_running(config):
        return "Server query", True, "Server not running (skipped)"
    query_port = None
    try:
        from app.monitoring.a2s_monitor import query_server_sync

        from app.server.server_status import get_steam_query_port

        query_port = get_steam_query_port(config)
        result = query_server_sync("127.0.0.1", query_port, timeout=3.0)
        if result.online:
            return (
                "Server query",
                True,
                f"Visible — {result.player_count}/{result.max_players} players, map: {result.map_name}",
            )
        return (
            "Server query",
            False,
            f"Not responding: {result.error}\n"
            f"  Check steamQueryPort in Server DayZ Config → Network "
            f"(current: {query_port}) and ensure your firewall allows UDP on that port",
        )
    except Exception as exc:
        port_info = f" (current: {query_port})" if query_port is not None else ""
        return (
            "Server query",
            False,
            f"Query failed: {exc}\n"
            f"  Check steamQueryPort in Server DayZ Config → Network"
            f"{port_info} and ensure your firewall allows UDP on that port",
        )


def _check_traderplus(config: DayzConfig) -> CheckResult | None:
    """Check TraderPlus mod setup. Returns None if the mod is not in the mod list."""
    from app.xml.economycore_xml import get_economycore_path, parse_economycore_file
    from app.shared.json_utils import validate_json_file
    from app.shared.seed_data import MOD_DEPS
    from app.traderplus.trader_common import (
        get_traderplus_config_dir,
        has_traderplus_types_source,
        is_mod_active,
        _profiles_dir,
        _traderplus_mod_dir,
    )
    from app.xml.xml_utils import get_mission_dir

    if not is_mod_active(config, ["@TraderPlus"]):
        return None

    issues: list[str] = []

    # 4. Mod installed + dependencies
    if not _traderplus_mod_dir(config).is_dir():
        issues.append("@TraderPlus mod folder not found — run update")
    for dep_info in MOD_DEPS.values():
        if dep_info["name"] != "@TraderPlus":
            continue
        for dep_name in dep_info.get("deps", {}).values():
            if not (config.server_dir_path / dep_name).is_dir():
                issues.append(f"{dep_name} dependency missing — add & install it")

    # 1 + 2. Types XML file + cfgeconomycore registration. Not when the admin
    # turned the mod's copy off, deleted it or took it over on the Custom config
    # page: then it is absent on purpose, and the two false issues sent
    # them to "reset XML files" on every run.
    from app.xml.xml_manual import is_excluded

    # Nor when the installed mod ships no types XML (the 2026-11-09 Workshop
    # version): no merge can make the file, so "reset XML files" cannot help.
    tp_types = "traderplus-TraderPlus_types.xml"
    turned_off = is_excluded(tp_types, manual_dir=config.paths.custom_xml_dir)
    no_source = _traderplus_mod_dir(config).is_dir() and not has_traderplus_types_source(config)
    mission_dir = get_mission_dir(config)
    if mission_dir and not turned_off and not no_source:
        types_file = mission_dir / "custom" / "traderplus-TraderPlus_types.xml"
        if not types_file.is_file():
            issues.append("custom/traderplus-TraderPlus_types.xml missing — reset XML files")
        econ_path = get_economycore_path(mission_dir)
        if econ_path:
            try:
                ef = parse_economycore_file(econ_path)
                registered = any(
                    f.name == "traderplus-TraderPlus_types.xml" and f.type == "types"
                    for rc in ef.rootclasses if rc.folder == "custom"
                    for f in rc.files
                )
                if not registered:
                    issues.append("types XML not registered in cfgeconomycore.xml — reset XML files")
            except (OSError, ET.ParseError):
                pass

    # 3. Profiles TraderPlus folder + JSON validation
    config_dir = get_traderplus_config_dir(config)
    if config_dir is None:
        tp_dir = _profiles_dir(config) / "TraderPlus"
        if not tp_dir.is_dir():
            issues.append("profiles/TraderPlus folder missing — start server once or reset JSON files")
        else:
            issues.append("profiles/TraderPlus/TraderPlusConfig missing — reset JSON files")
    else:
        json_files = list(config_dir.glob("*.json"))
        if not json_files:
            issues.append("no JSON configs in TraderPlusConfig — reset JSON files")
        else:
            broken = [jf.name for jf in json_files if not validate_json_file(jf)]
            if broken:
                detail = "; ".join(broken[:2])
                if len(broken) > 2:
                    detail += f" (+{len(broken) - 2} more)"
                issues.append(f"invalid JSON — {detail}")

    if not issues:
        if turned_off:
            return "TraderPlus", True, (
                "Off: the mod's TraderPlus_types.xml is turned off or taken over on the "
                "Custom config page; the rest of the setup is OK"
            )
        if no_source:
            return "TraderPlus", True, (
                "Off: this TraderPlus version ships no types XML; the rest of the setup is OK"
            )
        return "TraderPlus", True, "Mod setup OK"
    return "TraderPlus", False, "; ".join(issues)


LOAD_ORDER_LABEL = "Mod load order"


def _check_load_order(config: DayzConfig) -> CheckResult | None:
    """Manual load order (plans/49 D2): each mod it puts before a mod that mod
    requires. None in automatic mode, where the dependency sort rules it out."""
    from app.modmgr import modlist
    from app.modmgr.mods import load_order_breaks

    if not modlist.load(config).manual:
        return None

    breaks = load_order_breaks(config)
    if not breaks:
        return LOAD_ORDER_LABEL, True, "Manual; every mod loads after the mods it requires"
    shown = "; ".join(f"{mod} loads before {dep}, which it requires" for mod, dep in breaks[:3])
    if len(breaks) > 3:
        shown += f" (+{len(breaks) - 3} more)"
    return LOAD_ORDER_LABEL, False, f"Manual: {shown}"


def _check_preconfigured(config: DayzConfig, module) -> CheckResult | None:
    """One pre-configured mod's row: *module* has ``LABEL`` and ``doctor_row``.
    None when the mod is not installed, so the row appears only with it."""
    try:
        return module.doctor_row(config)
    except Exception as e:  # noqa: BLE001 - one mod's check must not sink the Doctor
        return module.LABEL, False, f"Check failed: {e}"


def _check_expansion_ai(config: DayzConfig) -> CheckResult | None:
    """Expansion AI's own patrols against its load rules (plans/64)."""
    from app.ops import expansion_ai

    return _check_preconfigured(config, expansion_ai)


def _check_dynamic_ai(config: DayzConfig) -> CheckResult | None:
    """Dynamic AI's settings against the mod's own load rules (plans/47 B)."""
    from app.ops import dynamic_ai

    return _check_preconfigured(config, dynamic_ai)


def _check_radzone(config: DayzConfig) -> CheckResult | None:
    """RadZone's zone and crate files against their own shape (plans/48 A)."""
    from app.ops import zone_mods

    return _check_preconfigured(config, zone_mods)


def _check_toxiczone(config: DayzConfig) -> CheckResult | None:
    """ToxicZone's zones, moving paths and load order (plans/48, ToxicZone)."""
    from app.ops import toxic_zone

    return _check_preconfigured(config, toxic_zone)


def list_mission_xml_files(config: DayzConfig) -> list[Path]:
    """Every XML file under the current mission folder, sorted. [] when the
    mission can't be resolved. Shared by the XML-files check and the web
    Doctor report (which lists the names in a tooltip)."""
    try:
        from app.modmgr.mods import get_current_mission
        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            return []
        mission_dir = config.server_dir_path / "mpmissions" / mission
        if not mission_dir.is_dir():
            return []
        return sorted(mission_dir.rglob("*.xml"))
    except Exception:
        return []


def _check_xml_files(config: DayzConfig) -> CheckResult:
    """Validate all XML files in the mission folder parse without errors."""
    try:
        from app.modmgr.mods import get_current_mission
        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            return "XML files", False, "Could not determine mission folder"
        mission_dir = config.server_dir_path / "mpmissions" / mission
        if not mission_dir.is_dir():
            return "XML files", False, f"Mission dir missing: {mission_dir}"

        xml_files = list_mission_xml_files(config)
        if not xml_files:
            return "XML files", True, "No XML files found"

        broken: list[str] = []
        for f in xml_files:
            try:
                load_xml(f)
            except ET.ParseError as e:
                line, col = getattr(e, "position", (0, 0))
                name = f.relative_to(mission_dir)
                reason = friendly_xml_error(str(e))
                broken.append(f"{name}:{line} — {reason}")
            except Exception as e:
                name = f.relative_to(mission_dir)
                broken.append(f"{name}: {e}")

        if broken:
            detail = "; ".join(broken[:3])
            if len(broken) > 3:
                detail += f" (+{len(broken) - 3} more)"
            return "XML files", False, detail
        return "XML files", True, f"{len(xml_files)} files OK"
    except Exception as e:
        return "XML files", False, str(e)


def _get_local_ip() -> str:
    """The source address the kernel would use for the default route.

    **Not "the local IP"**, which is what every surface used to label it. No
    packet is sent — connecting a UDP socket only picks a route — so what comes
    back is whichever address is on the outbound interface: the **public** one
    on a VPS, a private one behind NAT. Measured on a VPS, it printed
    the public IPv4 under the heading `Local IP` (F10). Callers must label it
    "Primary IP" or say which it is; the value was always right.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _print_server_info(config: Optional[DayzConfig]) -> None:
    """Print server ports and local IP address."""
    if config is None:
        return

    from app.server.instances import instance_ports

    table = Table(title="Server Info", expand=False)
    table.add_column("", style="cyan")
    table.add_column("Value")

    # See `_get_local_ip` — "Local IP" was a lie on every VPS.
    local_ip = _get_local_ip()
    table.add_row("Primary IP", local_ip)

    # Game port
    game_port = config.server_port
    table.add_row("Game port", str(game_port))

    # The Instances page's rule, so an instance not set up yet shows its own
    # port rather than serverDZ.cfg's default 27016.
    table.add_row("Steam query port", str(instance_ports(config).query))

    # BattlEye / RCON port
    rcon_port = config.rcon_port
    if config.rcon_password:
        table.add_row("BattlEye / RCON port", str(rcon_port))
    else:
        table.add_row("BattlEye / RCON port", f"{rcon_port} [dim](not configured)[/dim]")

    console.print(table)
    console.print(
        "[dim]Primary IP is the address on this box's default route — the public "
        "one on a VPS, a private one behind NAT.[/dim]"
    )
    console.print()


def _print_service_files(config: Optional[DayzConfig]) -> None:
    """Print service/bat file contents with syntax highlighting."""
    from rich.syntax import Syntax

    if config is None:
        return

    server_dir = config.server_dir_path

    if IS_WINDOWS:
        for name, lexer in [("start-server.bat", "batch"), ("stop-server.bat", "batch")]:
            path = server_dir / name
            if path.exists():
                console.print(f"[bold]{path}[/bold]")
                console.print(Syntax(path.read_text(encoding="utf-8"), lexer, theme="monokai", line_numbers=True))
                console.print()
            else:
                console.print(f"[dim]{name}: not found[/dim]")
    else:
        from app.server.panel_unit import PANEL_SERVICE_NAME
        from app.shared.systemd_scope import get_scope, server_unit

        # The scope's unit dir, as `_check_service` reads it. A hardcoded
        # /etc/systemd/system called a user unit "not installed" below a
        # Service row that had just found it.
        unit_dir = get_scope(config).unit_dir
        for name in (server_unit(config), PANEL_SERVICE_NAME):
            service_path = unit_dir / name
            if service_path.exists():
                console.print(f"[bold]{service_path}[/bold]")
                console.print(Syntax(service_path.read_text(encoding="utf-8"), "ini", theme="monokai", line_numbers=True))
                console.print()
            else:
                console.print(f"[dim]{name}: not installed ({service_path})[/dim]")


def _check_mod_conflicts(config: DayzConfig) -> tuple[list[CheckResult], "ScanResult | None"]:
    """Run mod conflict checks — RPT errors, deps, bikeys, requiredAddons.

    Returns (check_results, scan_result) so the caller can render the dep graph.
    """
    from app.modmgr.mod_conflict import scan_conflicts

    try:
        result = scan_conflicts(config)
    except Exception:
        return [("Mod conflicts", False, "Scan failed")], None

    results: list[CheckResult] = []
    for check_name, (passed, detail) in result.check_statuses.items():
        results.append((check_name, passed, detail))

    return results, result


def _run_all_checks(
    config: Optional[DayzConfig], *, probe_steam: bool = False
) -> tuple[list[CheckResult], "ScanResult | None"]:
    """Run all health checks. Returns (results, scan_result).

    scan_result is from mod conflict scan (for dependency graph rendering).

    ``probe_steam`` adds the Steam-login row and is **off by default on
    purpose**. It spawns SteamCMD, and this function is also what the web
    Doctor page builds its report from and what the notifier polls — a probe on
    either would put a second SteamCMD next to a running download (see the
    STEAMROOT rule in the root CLAUDE.md) and add up to 90s to each. Only
    ``zsm doctor --steam``, typed by a person, passes True.
    """
    results: list[CheckResult] = []
    scan_result = None

    # Check 1: Config
    if config is None:
        label, ok, detail = _check_config()
        results.append((label, ok, detail))
        if not ok:
            skipped = ["Server directory", "SteamCMD", MOD_LIST_LABEL, "Mission folder", "XML files", "Server port", "Service"]
            if probe_steam:
                skipped.insert(2, "Steam login")
            for lbl in skipped:
                results.append((lbl, False, "Skipped (no config)"))
            return results, None
        config = DayzConfig.load()
    else:
        results.append(("Apps config file", True, "Loaded"))

    results.append(_check_server_dir(config))
    volume = _check_server_volume(config)
    if volume is not None:
        results.append(volume)
    if server_is_installed(config):
        results.append(_check_server_branch(config))
    results.append(_check_steamcmd(config))
    if probe_steam:
        results.append(_check_steam_login(config))

    os_deps = _check_os_deps(config)
    if os_deps is not None:
        results.append(os_deps)

    results.append(_check_mod_list(config))
    load_order = _check_load_order(config)
    if load_order is not None:
        results.append(load_order)
    # Both read files that only exist once the server is installed — with no
    # install they add nothing to the Server-directory finding above.
    if server_is_installed(config):
        results.append(_check_mission(config))
        results.append(_check_xml_files(config))
        fps = _check_fps_logging(config)
        if fps is not None:
            results.append(fps)
    results.append(_check_port(config))

    fw = _check_firewall(config)
    if fw is not None:
        results.append(fw)

    tls_result = _check_tls_cert(config)
    if tls_result is not None:
        results.append(tls_result)

    results.append(_check_service(config))

    panel_result = _check_panel_service(config)
    if panel_result is not None:
        results.append(panel_result)

    scope_result = _check_service_scope(config)
    if scope_result is not None:
        results.append(scope_result)

    crash_result = _check_crash_loop(config)
    if crash_result is not None:
        results.append(crash_result)

    results.extend(_vps_checks(config))

    tp_result = _check_traderplus(config)
    if tp_result is not None:
        results.append(tp_result)

    for mod_check in (_check_expansion_ai, _check_dynamic_ai, _check_radzone, _check_toxiczone):
        mod_result = mod_check(config)
        if mod_result is not None:
            results.append(mod_result)

    results.append(_check_server_query(config))

    conflict_checks, scan_result = _check_mod_conflicts(config)
    results.extend(conflict_checks)

    return results, scan_result


def get_failed_checks(config: DayzConfig) -> list[CheckResult]:
    """Return only the failed checks for the given config.

    Used by the web notifier's doctor loop to report new issues.
    """
    checks, _ = _run_all_checks(config)
    return [(label, ok, detail) for label, ok, detail in checks if not ok]


def _print_conflict_scan(scan_result) -> None:
    """Print conflict issues table and dependency graph from shared helpers."""
    if not scan_result:
        return

    from app.modmgr.mod_conflict import build_issues_table, build_dep_tree

    table = build_issues_table(scan_result)
    if table:
        console.print(table)
        console.print()
        if scan_result.error_count:
            console.print(
                f"[red]{scan_result.error_count} error(s)[/red], "
                f"[yellow]{scan_result.warning_count} warning(s)[/yellow]"
            )
        else:
            console.print(
                f"[yellow]{scan_result.warning_count} warning(s)[/yellow], no errors"
            )
        console.print()

    tree = build_dep_tree(scan_result)
    if tree:
        console.print()
        console.print(tree)


def _checks_table(title: str, checks: list[CheckResult]) -> tuple[Table, bool]:
    """One section's Rich table, plus whether anything in it failed."""
    table = Table(title=title, expand=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status", justify="center")
    # Folded, not cut: a path or a link has no space to wrap at, and Rich's
    # default ends such a word in "…". The Windows runtime row's installer
    # path and download link were both cut that way in a 84-column terminal.
    table.add_column("Detail", overflow="fold")

    any_fail = False
    for label, ok, detail in checks:
        if ok:
            status = "[green]✅ OK[/green]"
        else:
            status = "[yellow]⚠︎  WARN[/yellow]"
            any_fail = True
        table.add_row(label, status, detail)
    return table, any_fail


def _run_preflight(ctx: click.Context) -> None:
    """`zsm doctor --preflight` — the host checks only.

    Deliberately skips `prompt_init_if_missing` and tolerates a missing config:
    the point is answering "is this box set up right?" on a box where nothing is
    installed yet. Reads only; fixing is `zsm bootstrap-vps` (TODO-2 A3).
    """
    from app.ops.vps_readiness import probes, verdict

    try:
        config: Optional[DayzConfig] = DayzConfig.load()
    except Exception:
        config = None

    found = probes(config)
    if not found:
        console.print()
        console.print("[yellow]VPS readiness checks are Linux-only.[/yellow]")
        return

    table, any_fail = _checks_table(
        SECTION_VPS, [(p.label, p.ok, p.detail) for p in found]
    )
    values = {p.label: p.value for p in found}
    ram = values.get("RAM")
    line = verdict(int(ram) if ram is not None else None, values.get("CPU steal"))

    console.print()
    console.print(table)
    console.print()
    console.print(f"[bold]Verdict:[/bold] {line}")
    console.print(
        "[dim]Evaluating a box you don't own yet? Run preflight.sh on it first:[/dim]"
    )
    console.print(
        "[dim]  curl -fsSL "
        "https://raw.githubusercontent.com/robba86/zedstead/main/preflight.sh "
        "-o /tmp/zsm-preflight.sh && sh /tmp/zsm-preflight.sh[/dim]",
        soft_wrap=True,
    )
    if any_fail:
        ctx.exit(1)


@click.command(name="doctor")
@click.option(
    "--preflight",
    is_flag=True,
    help="Run only the VPS-readiness checks. Works before the server is "
    "installed and needs no config. Exits non-zero if any of them warn.",
)
@click.option(
    "--steam",
    is_flag=True,
    help="Also check that SteamCMD's cached login still works. Off by "
    "default: it runs SteamCMD, which must not happen next to an update "
    "job, and adds up to 90s.",
)
@click.pass_context
def doctor_cmd(ctx: click.Context, preflight: bool, steam: bool) -> None:
    """Validate server setup health.

    Exits 1 if any check warns, so it can gate a cron job, a CI step or a
    cloud-init run. `--preflight` narrows it to the host checks.

    `--steam` is opt-in for that same reason: this command is built to be
    cron'd, and a scheduled doctor spawning SteamCMD beside a scheduled update
    is how the cached login token and the workshop manifest get corrupted.
    """
    if preflight:
        _run_preflight(ctx)
        return

    from app.commands.init import prompt_init_if_missing

    if not prompt_init_if_missing(ctx):
        return

    try:
        config: Optional[DayzConfig] = DayzConfig.load()
    except Exception:
        config = None

    checks, scan_result = _run_all_checks(config, probe_steam=steam)

    any_fail = False
    console.print()
    for section, title in ((SECTION_SERVER, "Server Health Checks"), (SECTION_VPS, SECTION_VPS)):
        rows = [c for c in checks if section_for(c[0]) == section]
        if not rows:
            continue
        table, failed = _checks_table(title, rows)
        any_fail = any_fail or failed
        console.print(table)
        console.print()

    if any_fail:
        console.print(
            "[yellow]⚠ Some checks failed — the Detail column above says what "
            "and how. This command exits 1.[/yellow]"
        )

    _print_conflict_scan(scan_result)

    _print_server_info(config)

    # Print service file contents below the table
    _print_service_files(config)

    # Last thing that happens, so every table above still prints. `--preflight`
    # has always exited 1 on a warning and promised so in its own --help, while
    # the full form printed "Some checks failed" and exited **0** — so the
    # *narrower* command was the only one usable as a gate, and `zsm doctor`
    # was useless in a cron job, a CI step or a cloud-init check. Measured on
    # the VPS test: five WARNs, the warning line, exit 0 (F8).
    #
    # This does not change what counts as a failure. Those rows already render
    # as WARN and the line above already says some checks failed; only the exit
    # code was disagreeing with the screen.
    if any_fail:
        ctx.exit(1)
