"""Remediation hints — the "how do I fix this" half of a finding.

``app/ops/doctor_report.py`` already routes *health-check rows* into the view
that fixes them. Conflict findings (``app.modmgr.mod_conflict.ConflictItem``)
had no such half at all: both tables that render them — the Doctor page's Mod
Conflicts card and the Mods page's Conflicts tab — printed a category, a mod and
a message, and left the admin to work out what to do about it.

This module owns both halves of the answer:

* the web-UI routes, **moved here from** ``doctor_report`` so the Mods router can
  build the same chips without importing the Doctor report (``doctor_report``
  re-imports them, so ``doctor_report.MODS_INSTALLED`` still resolves), and
* ``conflict_fix(item)`` — per-category ``{why, how, links, actions}``.

``links`` are chips that route somewhere; ``actions`` are buttons the page runs
in place, and every one of them reuses an endpoint that already exists
(``POST /api/mods/install``, ``POST /api/mods/copy-bikeys``). An action carrying
``confirm`` is asked about first — it deletes or overwrites files.

Both React tables render this through ``webui/src/components/FixCell.jsx``, so a
hint added here shows up on both pages at once. Keep the wording free of Rich
markup: it is rendered as plain text.
"""

from __future__ import annotations

# ── Deep links into the web UI ──────────────────────────────────────────
#
# Routes live server-side (same precedent as ``configs.py::_RUNCOMMAND_LINKS``).
MODS_INSTALLED = "/mods?tab=Installed"
MODS_RECONFIGURE = "/mods?tab=Installed#reconfigure-xml"  # flashes the button
MODS_CONFLICTS = "/mods?tab=Conflicts"
MODS_INSTALL_SEARCH = "/mods?tab=Install&subtab=Search"
SETTINGS = "/settings"
INSTANCES = "/instances"
MAPS = "/maps"
CLEANUP_TRADERPLUS = "/cleanup#reset-traderplus"
ECONOMYCORE = "/configs/economycore"
ANALYSIS = "/analysis"
ANALYSIS_LOGS = "/analysis?tab=Server+logs"
# Config editors a log finding can point at (``/configs/:id`` — the ids are the
# EditorSpec ids in app/web/editors/). ``CONFIG_ECONOMYCORE`` is the same route
# as ``ECONOMYCORE`` above under the naming the newer callers use; both stay so
# neither set of imports has to churn.
CONFIGS = "/configs"
CONFIG_ECONOMYCORE = ECONOMYCORE
CONFIG_SERVERDZ = "/configs/serverdz"
CONFIG_TYPES = "/configs/types"
CONFIG_EVENTS = "/configs/events"
CONFIG_EVENTSPAWNS = "/configs/eventspawns"
CONFIG_MESSAGES = "/configs/messages"
CONFIG_IGNORELIST = "/configs/ignorelist"
CONFIG_RANDOMPRESETS = "/configs/randompresets"
CONFIG_PLAYERSPAWNPOINTS = "/configs/playerspawnpoints"
CONFIG_EXPANSION_AI = "/configs/expansion-ai"
CONFIG_EXPANSION_AI_PATROLS = "/configs/expansion-ai-patrols"
CONFIG_EXPANSION_AI_PATROL_LIST = "/configs/expansion-ai-patrol-list"
MAP_EDITOR_AI_PATROLS = "/map-editor?layers=expaipatrols"
MOD_XML = "/configs?tab=Custom"
# SettingsPage's app-wide ?q= prefill lands on the field itself.
SETTINGS_LOG_KEEP = "/settings?q=log_cleanup_keep"


# ── Actions (buttons the page runs in place) ────────────────────────────


def _copy_bikeys_action() -> dict:
    """Re-sync ``keys/`` from the enabled mods — ``POST /api/mods/copy-bikeys``.

    The targeted fix for ``missing_bikey``: it runs the same
    ``fileops.copy_bikey_files`` the install job and ``zsm run`` run, and it
    reads the mod list from ``get_active_mods`` — the same source the check
    itself compares against, so what it copies is exactly what was reported
    missing.
    """
    return {
        "kind": "copy_bikeys",
        "label": "Copy bikeys",
        "confirm": (
            "Clears the server's keys/ folder (except dayz.bikey) and re-copies the .bikey "
            "files from every enabled mod. Restart the server afterwards — BattlEye only "
            "reads keys/ at startup."
        ),
    }


def _reinstall_action(mod_name: str) -> dict:
    """Re-run the install job for an already-installed mod.

    Installing is idempotent (the mod list and the load order are, and SteamCMD
    re-downloads), so this is the repair for a half-copied or stale mod: it
    re-downloads, re-copies the mod folder *and* its keys. By name, not id — the
    conflict scan only knows names, and the endpoint resolves the id itself.
    """
    return {
        "kind": "install_mods",
        "label": f"Reinstall {mod_name}",
        "mods": [{"name": mod_name, "id": ""}],
        "confirm": (
            f"Re-downloads {mod_name} from the Workshop and copies it over the installed "
            "copy, then re-merges the mission XML and regenerates the launch command."
        ),
    }


def _install_dep_action(dep_name: str, dep_id: str) -> dict:
    """Install one missing dependency. ``dep_id`` is the reliable key when the
    finding carries one (``MOD_DEPS`` knows it); the name is the fallback."""
    return {
        "kind": "install_mods",
        "label": f"Install {dep_name or dep_id}",
        "mods": [{"name": dep_name, "id": dep_id}],
    }


# ── Per-category hints ──────────────────────────────────────────────────


def conflict_fix(item) -> dict | None:
    """``{why, how, links, actions}`` for one conflict finding, else ``None``.

    ``item`` is duck-typed on the ``ConflictItem`` fields (``category``,
    ``mod_name``, ``dep_name``, ``dep_id``) so a serialised copy works too.
    An unknown category returns ``None`` — the tables then render the row as
    they always did rather than inventing advice for a finding this module has
    never seen.
    """
    category = getattr(item, "category", "") or ""
    mod = getattr(item, "mod_name", "") or ""

    if category == "missing_bikey":
        return {
            "why": (
                "The server's keys/ folder has no .bikey for this mod, so BattlEye rejects "
                "every client that loads it — players are kicked for a bad signature."
            ),
            "how": (
                "Re-sync keys/ from the enabled mods. If the mod's own Keys/ folder is empty "
                "too the download was incomplete — reinstall it instead. Either way the server "
                "has to be restarted before the keys count."
            ),
            "links": [{"label": "Installed mods", "to": MODS_INSTALLED}],
            "actions": [_copy_bikeys_action()] + ([_reinstall_action(mod)] if mod else []),
        }

    if category == "missing_dep":
        dep_name = getattr(item, "dep_name", "") or ""
        dep_id = str(getattr(item, "dep_id", "") or "")
        actions = [_install_dep_action(dep_name, dep_id)] if (dep_name or dep_id) else []
        return {
            "why": (
                f"{dep_name or 'A required mod'} is a hard dependency of "
                f"{mod or 'an installed mod'} and is not installed. Dependent mods fail to "
                "compile their scripts, which usually stops the server from starting at all."
            ),
            "how": (
                "Install the dependency — Zedstead already knows its workshop ID, and installing "
                "it adds it to the mod list and the launch command as well."
            ),
            "links": [{"label": "Install a mod", "to": MODS_INSTALL_SEARCH}],
            "actions": actions,
        }

    if category == "required_addon":
        return {
            "why": (
                "One of this mod's PBOs declares a requiredAddon that no installed mod "
                "provides. Usually the mod shipping that addon is missing, is installed but "
                "disabled, or was only half-copied by an interrupted download."
            ),
            "how": (
                "Check the mod's Workshop page for what it requires and install that mod; if "
                "you turned it off, re-enable it on the Installed tab. Reinstalling this mod "
                "repairs a partial copy."
            ),
            "links": [
                {"label": "Installed mods", "to": MODS_INSTALLED},
                {"label": "Find a mod", "to": MODS_INSTALL_SEARCH},
            ],
            "actions": [_reinstall_action(mod)] if mod else [],
        }

    if category == "rpt_error":
        return {
            "why": (
                "The server's newest .RPT log recorded this while loading. Script and class "
                "errors are nearly always a mod built for a different game version, or a "
                "dependency that isn't there."
            ),
            "how": (
                "Read the surrounding log lines on the Analysis page for the full context, "
                "then update or reinstall the mod named here and confirm its dependencies are "
                "installed and enabled."
            ),
            "links": [
                {"label": "Log analysis", "to": ANALYSIS},
                {"label": "Installed mods", "to": MODS_INSTALLED},
            ],
            "actions": [_reinstall_action(mod)] if mod else [],
        }

    if category == "load_order":
        return {
            "why": (
                "Manual load order is on, and it loads this mod before a mod it requires. "
                "The server can then fail to compile the mod's scripts and stop at start."
            ),
            "how": (
                "On the Installed tab, drag the required mod above this one, or switch the "
                "load order back to automatic, which sorts every mod after what it requires."
            ),
            "links": [{"label": "Installed mods", "to": MODS_INSTALLED}],
            "actions": [],
        }

    if category == "no_logs":
        return {
            "why": (
                "There is no .RPT file in the profiles folder, so the log scan had nothing to "
                "read. Either the server has not run since the folder was last cleared, or log "
                "cleanup deleted the last one."
            ),
            "how": (
                "Start the server once and scan again. If log cleanup is on, keep at least one "
                "log per type so the newest one always survives."
            ),
            "links": [{"label": "Log cleanup setting", "to": SETTINGS_LOG_KEEP}],
            "actions": [],
        }

    return None
