"""Structured Doctor report — JSON-serialisable diagnostic data.

The CLI's ``zsm doctor`` prints Rich tables. The web app cannot reuse those
(they are markup-oriented), so this module gathers the same underlying data
through the same shared helpers (``app.commands.doctor``,
``app.ops.log_analysis``, ``app.modmgr.mod_conflict``) and returns a plain
``dict`` for ``GET /api/doctor``.

Every sub-section is nullable / empty-safe so a bare or unconfigured server still
returns a well-formed body (200) rather than raising.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.shared.platform import IS_WINDOWS


def _severity_of_check(ok: bool) -> str:
    """Health checks model status as a bool; a failure is a warning. The real
    error tier is carried by mod-conflict issues (ConflictItem.severity)."""
    return "ok" if ok else "warning"


# ── Deep links into the web UI ──────────────────────────────────────────
#
# A check's ``detail`` is prose built by ``app/commands/doctor.py`` for the CLI.
# The web Doctor page turns parts of it into router links so a finding leads
# straight to the view that fixes it. Two shapes, both per-check:
#   * ``link_phrases`` — every occurrence of ``phrase`` inside ``detail`` becomes
#     a link (the phrase must appear verbatim in the CLI wording).
#   * ``links`` — trailing action chips, for targets the wording doesn't name.
# Same idea as ``routers/configs.py::_RUNCOMMAND_LINKS``: routes live server-side.
# The route constants themselves live in ``app.ops.fix_hints`` — the Mods router
# builds the same chips for its own Conflicts tab, and importing the whole Doctor
# report to reach them would be backwards. Re-exported here, so existing
# ``doctor_report.MODS_*`` references keep resolving.
from app.ops.fix_hints import (
    CLEANUP_TRADERPLUS,
    CONFIG_EXPANSION_AI,
    CONFIG_EXPANSION_AI_PATROLS,
    CONFIG_SERVERDZ,
    ECONOMYCORE,
    INSTANCES,
    MAPS,
    MODS_CONFLICTS,
    MODS_INSTALLED,
    MODS_RECONFIGURE,
    SETTINGS,
    conflict_fix,
)

# Phrases the TraderPlus check emits verbatim (see doctor._check_traderplus).
# "reset XML files" and "add & install it" are deliberately absent: both are
# instructions, not names of a place — they render as action buttons instead
# (see ``_actions_for``), so linking the words too would be a second, weaker
# route to the same fix.
_TRADERPLUS_PHRASES = {
    "reset JSON files": CLEANUP_TRADERPLUS,
    "profiles/TraderPlus/TraderPlusConfig missing": CLEANUP_TRADERPLUS,
    "no JSON configs in TraderPlusConfig": CLEANUP_TRADERPLUS,
    "cfgeconomycore.xml": ECONOMYCORE,
}

# The TraderPlus detail phrase that means "the custom types XML is gone".
_TRADERPLUS_RESET_XML_PHRASE = "reset XML files"


_TOOLTIP_MAX_LINES = 45

# The log analyzer's warning when the installed unit's ``-mod=`` list is empty
# (``app/ops/log_analysis.py::validate_mods`` — keep in sync).
_EXECSTART_NO_MODS = "No mods found in ExecStart."


def _phrase(text: str, phrase: str, to: str) -> list[dict]:
    """[{phrase, to}] when the phrase is actually in the text, else []."""
    return [{"phrase": phrase, "to": to}] if phrase and phrase in text else []


def _links_for(label: str, ok: bool, detail: str) -> tuple[list[dict], list[dict]]:
    """(links, link_phrases) for one check row."""
    links: list[dict] = []
    phrases: list[dict] = []

    if label == "Apps config file":
        links.append({"label": "Settings", "to": SETTINGS})
    elif label == "Mod list":
        m = re.search(r"\d+ mod\(s\) listed", detail)
        if m:
            phrases += _phrase(detail, m.group(0), MODS_INSTALLED)
        else:
            links.append({"label": "Mods", "to": MODS_INSTALLED})
    elif label == "Server branch" and not ok:
        links.append({"label": "Instances", "to": INSTANCES})
    elif label == "Mod load order" and not ok:
        links.append({"label": "Mods", "to": MODS_INSTALLED})
    elif label == "Mission folder":
        # The passing detail is the bare mission name (e.g. dayzOffline.enoch).
        if ok:
            phrases += _phrase(detail, detail, MAPS)
        else:
            links.append({"label": "Change map", "to": MAPS})
    elif label == "Mod dependencies" and not ok:
        # The whole detail is "N missing" — the Conflicts-tab chip already goes
        # where that count is itemised, so linking the words too is redundant.
        links.append({"label": "Check mods", "to": MODS_CONFLICTS})
    elif label in ("Bikey signatures", "requiredAddons") and not ok:
        links.append({"label": "Check mods", "to": MODS_CONFLICTS})
    elif label == "Dynamic AI" and not ok and "-mod=" in detail:
        links.append({"label": "Mods", "to": MODS_INSTALLED})
    elif label == "Expansion AI" and not ok:
        links.append({"label": "AI Patrols", "to": CONFIG_EXPANSION_AI_PATROLS})
        if "Player factions" in detail:
            links.append({"label": "Expansion AI", "to": CONFIG_EXPANSION_AI})
    elif label == "FPS logging":
        # `?q=` filters each form to the one field (ConfigEditorDrawer's search key).
        # Not only when the row warns: logging that is simply off passes, and
        # its text names the same two fields.
        phrases += _phrase(detail, "logAverageFps", f"{CONFIG_SERVERDZ}?q=logAverageFps")
        phrases += _phrase(detail, "-doLogs", "/configs/runcommand?q=doLogs")
    elif label == "TraderPlus" and not ok:
        for phrase, to in _TRADERPLUS_PHRASES.items():
            phrases += _phrase(detail, phrase, to)
        links.append({"label": "Re-configure XML", "to": MODS_RECONFIGURE})

    return links, phrases


def _actions_for(
    label: str, ok: bool, detail: str, scan, *, server_installed: bool = True
) -> list[dict]:
    """Buttons that fix the finding in place, instead of routing somewhere else.

    Shapes (``DoctorPage.jsx`` renders one button per entry):
      * ``{kind: "install_mods", label, mods: [{name, id}]}`` — one per missing
        dependency, plus an "Install all" when there is more than one. Fed from
        the scan's ``missing_dep`` items, which already carry ``dep_id`` so the
        install goes by workshop id (the reliable key) when one is known.
      * ``{kind: "reset_traderplus_xml", label}`` — POSTs the cleanup reset.
      * ``{kind: "install_server", label}`` — runs the update pipeline's
        update_server + finalize steps. Deliberately NOT the reinstall endpoint:
        that rmtree's the server dir first, which would take mpmissions and
        profiles with it when the binary is merely missing.
      * ``{kind: "install_service", label}`` — POSTs the service regenerate,
        which writes and installs the unit (enabling it at boot stays a separate
        toggle on the Service Setup card).
    """
    actions: list[dict] = []

    if label == "Mod dependencies" and not ok:
        mods = []
        seen: set[str] = set()
        for item in scan.items if scan else []:
            if item.category != "missing_dep":
                continue
            name = item.dep_name or ""
            wid = str(item.dep_id or "")
            key = wid or name
            if not key or key in seen:
                continue
            seen.add(key)
            mods.append({"name": name, "id": wid})
        for mod in mods:
            actions.append(
                {
                    "kind": "install_mods",
                    "label": f"Install {mod['name'] or mod['id']}",
                    "mods": [mod],
                }
            )
        if len(mods) > 1:
            actions.append({"kind": "install_mods", "label": "Install all", "mods": mods})
    elif label == "TraderPlus" and not ok and _TRADERPLUS_RESET_XML_PHRASE in detail:
        actions.append({"kind": "reset_traderplus_xml", "label": "Reset XML files"})
    elif label == "Server directory" and not ok:
        actions.append({"kind": "install_server", "label": "Install server"})
    elif label == "Service" and not ok and server_installed:
        # Not before the server: the unit is written into the server dir, and
        # Install server installs the service too (the pipeline's finalize).
        actions.append({"kind": "install_service", "label": "Install service"})

    return actions


def build_doctor_report(config) -> dict:
    """Gather the full Doctor report as a JSON-serialisable dict.

    ``config`` may be ``None`` (mirrors the CLI no-config path); each section
    degrades to ``null``/empty rather than raising.
    """
    from app.commands.doctor import _run_all_checks

    results, scan = _run_all_checks(config)
    # The Server directory row passes exactly when doctor.server_is_installed does.
    server_installed = any(lbl == "Server directory" and passed for lbl, passed, _ in results)
    tooltips = _check_tooltips(config, scan)

    checks = []
    for label, ok, detail in results:
        detail = _detail_for(label, ok, detail, config)
        links, phrases = _links_for(label, ok, detail)
        checks.append(
            {
                "label": label,
                "severity": _severity_of_check(ok),
                "ok": ok,
                "detail": detail,
                "remediation": None,
                "links": links,
                "link_phrases": phrases,
                "actions": _actions_for(label, ok, detail, scan, server_installed=server_installed),
                "tooltip": tooltips.get(label),
            }
        )

    summary = {
        "ok": sum(1 for c in checks if c["severity"] == "ok"),
        "warning": sum(1 for c in checks if c["severity"] == "warning"),
        "error": sum(1 for c in checks if c["severity"] == "error"),
        "total": len(checks),
    }

    log_analysis = _log_analysis(config)
    return {
        "checks": checks,
        "summary": summary,
        "log_summary": _log_summary(log_analysis),
        "conflicts": _conflicts(scan),
        "server_info": _server_info(config),
        "disc_usage": _disc_usage(config),
        "log_analysis": log_analysis,
        "service_file": _service_file(config),
    }


def _log_summary(log_analysis: dict | None) -> dict | None:
    """The latest log's own counts, for the page headline and the dashboard tile.

    ``summary`` counts health checks only, so the headline said "All checks
    passing" above a Log Analyzer card listing an error (test pass 1, F48).
    These are the log report's counts, which already leave out what Bohemia's
    files and pre-configured mods cause (``origin``), so they are the admin's.
    """
    if not log_analysis or not log_analysis.get("latest_log"):
        return None
    counts = log_analysis.get("summary") or {}
    return {
        "error": int(counts.get("error", 0)),
        "warning": int(counts.get("warning", 0)),
        "notice": int(counts.get("notice", 0)),
        "latest_log": log_analysis["latest_log"],
    }


def _detail_for(label: str, ok: bool, detail: str, config=None) -> str:
    """Web-only detail tweaks. The CLI says just "Loaded" for the config check;
    the page shows *which* file is loaded next to its Settings link."""
    if label == "Apps config file" and ok:
        from app.shared import paths

        loaded = config.paths.config_file if config is not None else paths.CONFIG_FILE
        return f"Loaded — {loaded}"
    return detail


def _check_tooltips(config, scan) -> dict[str, str]:
    """Hover text for check rows whose one-line detail hides a list."""
    tips: dict[str, str] = {}

    resolved = _resolve_config(config)
    if resolved is not None:
        try:
            from app.commands.doctor import list_mission_xml_files
            from app.xml.xml_utils import get_mission_dir

            files = list_mission_xml_files(resolved)
            mission_dir = get_mission_dir(resolved)
            names = []
            for f in files:
                try:
                    names.append(f.relative_to(mission_dir).as_posix())
                except (ValueError, TypeError):
                    names.append(f.name)
            if names:
                # Keep the bubble a sane height — it can't scroll (moving the
                # pointer into it dismisses it).
                shown, rest = names[:_TOOLTIP_MAX_LINES], len(names) - _TOOLTIP_MAX_LINES
                if rest > 0:
                    shown = shown + [f"… +{rest} more"]
                tips["XML files"] = "\n".join(shown)
        except Exception:
            pass

    missing = [i.message for i in (scan.items if scan else []) if i.category == "missing_dep"]
    if missing:
        tips["Mod dependencies"] = "\n".join(missing)

    return tips


def _annotate_mod_validation(items: list[dict], mods_listed: int) -> list[dict]:
    """Web-only: make the "no mods in ExecStart" warning actionable.

    With mods in the list the fix is mechanical — regenerate the unit so
    ``-mod=`` is rebuilt from the load order, then re-merge the mission XML — so
    the page renders an "Install mods now" button (``action``). With no mods at
    all there is nothing to install, so it degrades to a link to the Mods page.
    """
    for item in items:
        if item.get("message") != _EXECSTART_NO_MODS:
            continue
        if mods_listed > 0:
            item["message"] = (
                f"{_EXECSTART_NO_MODS} {mods_listed} mod(s) in the mod list "
                "are missing from the launch command."
            )
            item["action"] = "install_mods"
        else:
            item["message"] = f"{_EXECSTART_NO_MODS} No mods installed yet."
            item["links"] = [{"label": "Manage mods", "to": MODS_INSTALLED}]
    return items


def _conflicts(scan) -> dict:
    if scan is None:
        return {
            "issues": [],
            "error_count": 0,
            "warning_count": 0,
            "dependencies": {"pbos_scanned": 0, "edges": []},
        }
    dbg = scan.pbo_debug
    return {
        "issues": [
            {
                "severity": i.severity,
                "category": i.category,
                "message": i.message,
                "mod_name": i.mod_name,
                "detail": i.detail,
                # Only "missing_dep" carries these — the web page links the dep
                # name in the message to its mod page (and knows it is a mod, not
                # an addon class name it must leave as plain text).
                "dep_id": i.dep_id,
                "dep_name": i.dep_name,
                # What the finding means and how to fix it, plus the chips and
                # in-place buttons that do so (app/ops/fix_hints.py). The Mods
                # page's Conflicts tab attaches the same key to the same items,
                # and both render it with the one FixCell component.
                "fix": conflict_fix(i),
            }
            for i in scan.items
        ],
        "error_count": scan.error_count,
        "warning_count": scan.warning_count,
        "dependencies": {
            "pbos_scanned": dbg.pbos_scanned if dbg else 0,
            "edges": [
                {"mod": e.mod, "depends_on": e.depends_on, "source": e.source}
                for e in (dbg.dep_edges if dbg else [])
            ],
        },
    }


def _resolve_config(config):
    """Return a usable config or None (never raise)."""
    if config is not None:
        return config
    try:
        from app.shared.config import DayzConfig

        return DayzConfig.load()
    except Exception:
        return None


def _server_info(config) -> dict | None:
    config = _resolve_config(config)
    if config is None:
        return None
    try:
        from app.commands.doctor import _get_local_ip
        from app.server.instances import instance_ports

        return {
            "local_ip": _get_local_ip(),
            "game_port": config.server_port,
            # The Instances page's rule: an added instance's port lives in its
            # config until setup writes serverDZ.cfg.
            "steam_query_port": instance_ports(config).query,
            "rcon_port": config.rcon_port,
            "rcon_configured": bool(config.rcon_password),
        }
    except Exception:
        return None


def _disc_usage(config) -> dict | None:
    config = _resolve_config(config)
    if config is None:
        return None
    try:
        from app.modmgr import modlist

        server_path = config.server_dir_path
        total_size = 0
        if server_path.is_dir():
            for f in server_path.rglob("*"):
                if f.is_file():
                    try:
                        total_size += f.stat().st_size
                    except OSError:
                        pass
        target = server_path if server_path.exists() else Path.home()
        disk = shutil.disk_usage(target)

        return {
            "config_file": str(config.paths.config_file),
            "server_dir": str(config.server_dir),
            "steamcmd_dir": str(config.steamcmd_dir),
            "workshop_dir": str(config.workshop_dir),
            "server_gb": round(total_size / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "total_gb": round(disk.total / (1024**3), 1),
            "mod_count": len(modlist.installed(config)),
        }
    except Exception:
        return None


def _log_analysis(config) -> dict | None:
    """The Log Analyzer card — ``app/ops/log_analysis.build_log_report``.

    The card used to build its own dict here from four regex counts. Everything
    it shows now comes from the shared engine, so the CLI, this card and the
    Analysis page's Logs tab cannot drift; all that is left to do here is the
    web-only annotation that turns "no mods in ExecStart" into a button.
    """
    config = _resolve_config(config)
    if config is None:
        return None
    try:
        from app.ops.log_analysis import build_log_report

        report = build_log_report(config)
        _annotate_mod_validation(report["mod_validation"], report["mods_listed"])
        return report
    except Exception:
        return None


def _service_file(config) -> dict | None:
    config = _resolve_config(config)
    if config is None:
        return None
    try:
        server_dir = config.server_dir_path
        if IS_WINDOWS:
            from app.commands.service import START_BAT_NAME, STOP_BAT_NAME

            files = []
            for name in (START_BAT_NAME, STOP_BAT_NAME):
                bat = server_dir / name
                exists = bat.exists()
                files.append(
                    {
                        "name": name,
                        "installed": exists,
                        "content": bat.read_text(encoding="utf-8") if exists else None,
                    }
                )
            return {
                "kind": "batch",
                "installed": any(f["installed"] for f in files),
                "files": files,
            }

        from app.shared.systemd_scope import get_scope, server_unit

        scope = get_scope(config)
        service_path = scope.unit_dir / server_unit(config)
        installed = service_path.exists()
        return {
            "kind": "systemd",
            "installed": installed,
            "scope": scope.name,
            "path": str(service_path),
            "content": service_path.read_text(encoding="utf-8") if installed else None,
        }
    except Exception:
        return None
