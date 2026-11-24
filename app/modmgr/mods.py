"""Shared mod utilities — read mods, cache, mission detection, exclusion checks."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from app.shared.zip_utils import is_bare_name

if TYPE_CHECKING:
    from app.shared.config import DayzConfig


# ---------------------------------------------------------------------------
# Shared exclusion list (deduplicated from all config_*.py files)
# ---------------------------------------------------------------------------

EXCLUDED_MOD_KEYWORDS = [
    "namalsk",
    "banov",
    "deerisle",
    "chernarus",
    "livonia",
    "takistan",
    "melkart",
    "valning",
    "chiemsee",
    "exclusion",
    "iztek",
    "eden",
    "esseker",
]


def mod_is_excluded(mod_name: str, excluded_parts: list[str] | None = None) -> bool:
    parts = excluded_parts or EXCLUDED_MOD_KEYWORDS
    mod_lower = mod_name.lower()
    return any(p.lower() in mod_lower for p in parts)


def is_safe_mod_dir_name(name: str) -> bool:
    """True if *name* is a safe single-component mod dir like ``@CF``.

    Guards destructive ops (``shutil.rmtree``) against a name that would resolve
    to the server root or escape it: ``server_dir / ""`` is the server dir
    itself, and an absolute / ``../`` / ``a/b`` name points outside the intended
    target — an unguarded rmtree there would wipe the whole server directory.
    A real mod dir is always a single ``@Name`` path component.

    It is also one line: the name goes into the launch command and the unit
    file, where a character that ends a line splits it, so one is refused
    before the strip, wherever it sits.
    """
    if breaks_a_line(name):
        return False
    name = name.strip()
    return name.startswith("@") and is_bare_name(name)


def breaks_a_line(text: str) -> bool:
    """True if *text* holds a control character or a line or paragraph
    separator, i.e. anything `str.splitlines` splits a line at, or a lone
    surrogate (what JSON's ``"\\ud83d"`` decodes to), which no UTF-8 file can
    hold: writing one raised after the file had been opened and emptied."""
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp", "Cs") for ch in text)


def split_name_id(line: str) -> tuple[str, str] | None:
    """``(name, id)`` from one ``name=id`` line, both stripped, or None when
    it holds no "=".

    The one reader of that shape, seed_data's ``WORKSHOP_MOD_IDS``. Split at
    the last "=": an id is digits, and a folder name may hold one. What
    counts as an id is each caller's own check.
    """
    name, sep, mod_id = line.rpartition("=")
    return (name.strip(), mod_id.strip()) if sep else None


def apply_mod_state_to_mission_xml(
    config: DayzConfig, mod_name: str, *, enable: bool, log=None
) -> None:
    """Make the mission XMLs reflect a mod's new enabled/disabled state.

    Two halves, and both are needed: the mod's cfgeconomycore `<ce>` entries are
    commented out / restored in place, and every direct-merge mission file
    (cfgeventspawns.xml & co) is rebuilt for the current enabled set. Doing only
    the CE half — as this used to — left a disabled mod's spawn positions live in
    the mission until the next full `zsm run`.

    **Enabling also re-copies the mod's `custom/` files (2026 Q3).** Unwrapping
    the `<!-- DISABLED @Mod: … -->` comments puts the `<file>` registrations back,
    but the files they name are only still in `custom/` if no full merge ran while
    the mod was off — and one that did wiped the folder and skipped this mod. The
    result was cfgeconomycore.xml registering files that aren't there, which can
    fail the whole economy load. `merge_mod_economy_entries` re-copies and
    re-registers exactly this mod's, and the usual "a scoped merge deletes a
    disabled mod's parked wrappers" rule doesn't bite here: the unwrap above has
    already consumed them, and by this point the mod is enabled.
    """
    from app.xml.xml_economy import (
        disable_mod_economy_entries,
        enable_mod_economy_entries,
        merge_mod_economy_entries,
    )
    from app.modmgr import modlist
    from app.xml import xml_manual
    from app.xml.xml_merge import merge_all_direct_configs

    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return

    manual_dir = config.paths.custom_xml_dir
    if enable:
        enable_mod_economy_entries(config.server_dir_path, mission, mod_name)
        merge_mod_economy_entries(
            config.server_dir_path, mission, mod_name, manual_dir=manual_dir, log=log,
        )
    else:
        disable_mod_economy_entries(config.server_dir_path, mission, mod_name)
    # A manual file follows its mod (`xml_manual.mod_is_off`, Q1). The direct
    # half follows through the merge below.
    xml_manual.follow_mod_state(
        config.server_dir_path, mission, mod_name,
        enable=enable, manual_dir=manual_dir, log=log,
    )

    merge_all_direct_configs(
        config.server_dir_path,
        mission,
        manual_dir=manual_dir,
        disabled_mods=modlist.disabled(config),
        log=log,
    )


def reconfigure_mod_xml(config: DayzConfig, mod_name: str, log=None) -> dict:
    """Re-apply one mod's mission XML — CE-linked half + direct-merge half.

    The manual "Re-configure" counterpart to the full merge run by the update
    pipeline: only this mod's `custom/` files, cfgeconomycore `<ce>` entries and
    `<!-- START/END MOD -->` blocks are rebuilt, so every other mod's merged XML
    is left untouched.

    Callers must gate this on the mod being enabled — re-merging a disabled mod
    would put its XML back. Use `apply_mod_state_to_mission_xml` for state
    changes instead.

    Returns {"mission", "linked", "failed"}; "mission" is None when no mission
    could be resolved from serverDZ.cfg (nothing was changed).
    """
    from app.xml import xml_manual
    from app.xml.xml_economy import merge_mod_economy_entries
    from app.xml.xml_merge import merge_mod_direct_configs

    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        if log:
            log("Could not determine the current mission from serverDZ.cfg.")
        return {"mission": None, "linked": [], "failed": []}

    manual_dir = config.paths.custom_xml_dir
    if mod_name.lower() == "@traderplus":
        from app.traderplus.trader_common import UPSTREAM_TYPES_FILE_ID, ensure_upstream_defaults

        # A scoped merge does not copy manual files, so a new one is applied here.
        if ensure_upstream_defaults(config, log=log):
            xml_manual.apply_manual_file(
                config.server_dir_path, mission, UPSTREAM_TYPES_FILE_ID, manual_dir=manual_dir,
            )
    linked = merge_mod_economy_entries(
        config.server_dir_path, mission, mod_name, manual_dir=manual_dir, log=log,
    )
    failed = merge_mod_direct_configs(
        config.server_dir_path, mission, mod_name, manual_dir=manual_dir, log=log,
    )
    # Re-apply this mod's manual links after its blocks were rebuilt, so a link
    # that overrides the mod stays last in the file. Links owned by other mods
    # (or by none) are untouched — their blocks survived the scoped merge, and
    # the CE half re-registered their custom/ files off disk.
    failed += [
        f"manual link {file_id}"
        for file_id in xml_manual.apply_direct_files(
            config.server_dir_path, mission, manual_dir=manual_dir, only_mod=mod_name, log=log,
        )
    ]
    return {
        "mission": mission,
        "linked": [dest for dest, _type, _mod in linked],
        "failed": failed,
    }


def mark_disabled(config: DayzConfig, mod_name: str) -> bool:
    """Turn *mod_name* off in the mod list. Returns whether anything changed."""
    from app.modmgr import modlist

    return modlist.set_enabled(config, mod_name, False)


def mark_enabled(config: DayzConfig, mod_name: str) -> bool:
    """Turn *mod_name* on in the mod list, and take it out of ``update_triage``:
    an enabled mod is no longer set aside after an update, whoever enabled it
    (plans/73). Returns whether anything changed. The triage is in config.json,
    which this does **not** save: the CLI saves, the web goes through
    ``WebState.mutate_config``."""
    from app.modmgr import modlist

    changed = modlist.set_enabled(config, mod_name, True)
    if mod_name in config.update_triage:
        config.update_triage.pop(mod_name)
        changed = True
    return changed


def set_mod_disabled(config: DayzConfig, mod_name: str, log=None) -> None:
    """Turn a mod off and unmerge its mission XML."""
    mark_disabled(config, mod_name)
    apply_mod_state_to_mission_xml(config, mod_name, enable=False, log=log)


def set_mod_enabled(config: DayzConfig, mod_name: str, log=None) -> None:
    """Turn a mod on, re-merge its mission XML, and save config (the triage)."""
    if mark_enabled(config, mod_name):
        config.save()
    apply_mod_state_to_mission_xml(config, mod_name, enable=True, log=log)


def mark_server_side(config: DayzConfig, mod_name: str, server_side: bool) -> bool:
    """Load *mod_name* through ``-serverMod=`` or not. Returns whether
    anything changed."""
    from app.modmgr import modlist

    return modlist.set_server_only(config, mod_name, server_side)


# ---------------------------------------------------------------------------
# Workshop IDs
# ---------------------------------------------------------------------------

def is_workshop_id(value) -> bool:
    """True when *value* is a Workshop ID: a string of ASCII digits.

    The one test for an ID that is passed to SteamCMD or joined onto the
    workshop directory, wherever it came from (a file, a bundle, Steam's API).
    ``str.isdigit`` alone accepts other scripts' digits.
    """
    return isinstance(value, str) and value.isascii() and value.isdigit()


def is_placeholder_mod_name(name: str) -> bool:
    """True for ``@2792982069`` — a name that is only the workshop ID again.

    Such an entry is what an install writes when nothing could name the mod, so
    it must never be trusted as a *known* name: doing so pins the folder to the
    ID for good, even once zsm learns the real one.
    """
    return name.strip().lstrip("@").isdigit()


# ---------------------------------------------------------------------------
# Mission folder detection (from serverDZ.cfg)
# ---------------------------------------------------------------------------

def get_current_mission(server_cfg_path: Path) -> str | None:
    """The mission folder serverDZ.cfg names: the ``template`` inside
    ``class Missions { class DayZ { … } }`` (`servercfg.read_mission_template`).

    ``None`` when the value is not a single folder name. Every caller joins
    it onto ``mpmissions/`` and then reads, writes or deletes under it, so a
    template like ``../..`` would point them all outside the missions folder.
    """
    # Imported here: servercfg imports this module.
    from app.parsers.servercfg import read_mission_template

    value = read_mission_template(server_cfg_path)
    return _mission_folder(value) if value else None


def _mission_folder(value: str) -> str | None:
    return value if is_bare_name(value) else None


class MissionNotFound(RuntimeError):
    """serverDZ.cfg names no mission, or one with no folder in mpmissions/.
    The message is one sentence for the admin."""


def require_mission(config) -> str:
    """The mission folder serverDZ.cfg names, checked to exist in mpmissions/.

    Raises `MissionNotFound`. A typo in `template`, or a community map whose
    mission is not installed, gave `run xml-only` a FileNotFoundError traceback
    and the web merge job a raw "[Errno 2]" line (browser test 2026-11-17, F5).
    """
    try:
        mission = get_current_mission(config.server_cfg_path)
    except OSError:
        mission = None
    if not mission:
        raise MissionNotFound("Could not determine current mission from serverDZ.cfg.")
    if not (config.server_dir_path / "mpmissions" / mission).is_dir():
        raise MissionNotFound(f"serverDZ.cfg loads {mission}, but mpmissions/ has no such folder.")
    return mission


# ---------------------------------------------------------------------------
# Resolved mod load order (for display)
# ---------------------------------------------------------------------------

def get_resolved_mod_order(config: DayzConfig, *, include_disabled: bool = False) -> list[str]:
    """The order the server loads its mods in. `-mod=`, `-serverMod=`, the
    Mods page and ToxicZone's load-order finding all read this one function.

    Automatic (the default): every mod loads after the mods it requires (PBO
    requiredAddons + MOD_DEPS), and the saved order in mods.json breaks ties.
    Manual (mods.json's `load_order.manual`, plans/49): the saved order is the order, even
    where it puts a mod before one it requires. A mod the saved order does not
    list yet goes last, in automatic order among the others it lacks (D1).

    *include_disabled* resolves over every mod in the list instead: where a
    disabled mod would load if it were on. The Installed table sorts by that,
    so turning a mod Off leaves its row where it was (F78).
    """
    from app.commands.service import build_registry_order
    from app.modmgr import modlist
    from app.modmgr.mod_conflict import resolve_mod_load_order

    state = modlist.load(config)
    mod_list = state.names() if include_disabled else state.active()
    if not mod_list:
        return []
    saved: list[str] = []
    if state.manual:
        listed = set(mod_list)
        saved = [m for m in dict.fromkeys(state.pinned) if m in listed]
        placed = set(saved)
        mod_list = [m for m in mod_list if m not in placed]
    registry_order = build_registry_order(config)
    cached_ids = modlist.workshop_ids(config)
    return saved + resolve_mod_load_order(
        mod_list, config.server_dir_path, registry_order, cached_ids,
    )


def _keep_inactive_in_place(order: list[str], rows: list[str]) -> list[str]:
    """*order*, plus each mod of *rows* it lacks, put back after the mod it
    followed in *rows*.

    *order* is the enabled mods and *rows* every mod in the mod list. The enabled
    mods keep exactly *order*'s sequence, so the launch command is the same
    either way, and a disabled mod keeps its place for when it is turned on.
    """
    in_order = set(order)
    leading: list[str] = []
    after: dict[str, list[str]] = {}
    anchor = None
    for m in rows:
        if m in in_order:
            anchor = m
        elif anchor is None:
            leading.append(m)
        else:
            after.setdefault(anchor, []).append(m)
    out = leading
    for m in order:
        out.append(m)
        out.extend(after.get(m, ()))
    return out


def apply_load_order(
    config: DayzConfig, *, order: list[str] | None = None, manual: bool | None = None,
) -> None:
    """Save a load order and/or switch manual mode, in the mod list.

    The one write path for the Mods page, `zsm settings set
    manual_load_order` and the web Settings page (plans/49):

    - Switching manual on copies the order the server loads now into the
      pinned order, so the first launch command in manual mode is the last
      one in automatic mode. Nothing moves just because the switch was flipped.
    - Switching it off keeps the list. It breaks ties again, and the
      dependency sort moves whatever the list put in the wrong place.
    - A saved *order* holds the enabled mods. In manual mode each disabled mod
      stays after the mod it followed, so turning it back on puts it there.
    """
    from app.modmgr import modlist

    def pin(names: list[str]) -> None:
        modlist.update(config, lambda ml: setattr(ml, "pinned", list(names)))

    if manual and not modlist.load(config).manual:
        pin(_keep_inactive_in_place(
            get_resolved_mod_order(config), get_resolved_mod_order(config, include_disabled=True),
        ))
    if manual is not None:
        modlist.update(config, lambda ml: setattr(ml, "manual", manual))
    if order is not None:
        if modlist.load(config).manual:
            order = _keep_inactive_in_place(order, get_resolved_mod_order(config, include_disabled=True))
        pin(order)


def load_order_breaks(config: DayzConfig) -> list[tuple[str, str]]:
    """``(mod, required)`` for each enabled mod that loads before a mod it
    requires, in load order. Only manual mode puts one there; the automatic
    sort rules it out unless the mods require each other in a circle."""
    order = get_resolved_mod_order(config)
    deps = get_mod_load_deps(config)
    at = {m: i for i, m in enumerate(order)}
    return [(m, d) for m in order for d in sorted(deps.get(m, ())) if at.get(d, -1) > at[m]]


def get_mod_load_deps(config: DayzConfig) -> dict[str, set[str]]:
    """Return mod dependency constraints for the installed mods."""
    from app.modmgr import modlist
    from app.modmgr.mod_conflict import get_mod_dependencies

    mod_list = modlist.active(config)
    if not mod_list:
        return {}
    cached_ids = modlist.workshop_ids(config)
    return get_mod_dependencies(mod_list, config.server_dir_path, cached_ids)


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def kebab_case(name: str) -> str:
    """Convert '@Mod_Name' to 'mod-name'."""
    result = name.replace("@", "").replace("_", "-").replace(" ", "-").lower()
    result = re.sub(r'-+', '-', result)
    return result.strip("-")
