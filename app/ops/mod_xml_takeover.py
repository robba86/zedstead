"""Taking a mod's own XML file over, shared by the Mod XML page and the map editor.

"Take over" (`POST /api/modxml/source/fork`) copies one mod file into a manual
XML file, excludes the original, re-merges the mod without it and applies the
copy. The map editor's "Copy into a manual file and move" does the same before
it moves a mod's spawn position, so both go through `take_over_mod_source`.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from app.modmgr import modlist


def apply_source_change(config, key: str) -> dict:
    """Bring the mission in line with the source exclusions, for one mod.

    Excluding a file has to take effect now, not at the next merge — otherwise
    "disabled" means "still live until you remember to press Update". Which half
    runs is decided by what the file *is*: a CE fragment is re-linked through
    the scoped economy merge, anything else through the scoped direct merge,
    which strips the mod's blocks and re-splices only the files that survive the
    exclusion list.
    """
    from app.modmgr.mods import get_current_mission
    from app.xml.xml_classify import CE_KINDS, classify_mod_xml
    from app.xml.xml_economy import merge_mod_economy_entries
    from app.xml.xml_merge import merge_mod_direct_configs

    mod = PurePosixPath(key).parts[0]
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return {"mod": mod, "applied": False, "reason": "no mission"}
    # A disabled mod has its CE entries parked inside DISABLED wrappers that a
    # scoped merge would delete (app/xml/CLAUDE.md: per-mod re-configure is refused for
    # disabled mods). The exclusion is recorded; the next enable applies it.
    if mod in modlist.disabled(config):
        return {"mod": mod, "applied": False, "reason": "mod disabled"}

    path = config.server_dir_path / Path(*PurePosixPath(key).parts)
    kind = classify_mod_xml(path) if path.is_file() else None
    manual_dir = config.paths.custom_xml_dir
    if kind in CE_KINDS:
        merge_mod_economy_entries(config.server_dir_path, mission, mod, manual_dir=manual_dir)
    else:
        # The merge reads the stored exclusions itself, including the one just written.
        merge_mod_direct_configs(config.server_dir_path, mission, mod, manual_dir=manual_dir)
    return {"mod": mod, "applied": True, "kind": kind}


def take_over_mod_source(config, key: str, *, file_id: str = "") -> dict:
    """Copy the mod file *key* into a manual XML file and put the copy live.

    Returns the new file's record plus ``applied`` (and ``reason`` when it was
    not). Raises `xml_manual.ManualFileError` when the fork is refused.

    Created **enabled and applied**: the exclusion takes the mod's content out
    of the mission immediately, so leaving the copy unapplied would leave the
    mission with neither until the next merge. `apply_source_change` re-merges
    the mod without the excluded file first, then the manual file goes in —
    last, as always.
    """
    from app.modmgr.mods import get_current_mission
    from app.xml import xml_manual
    from app.xml.xml_utils import one_version_per_file

    manual_dir = config.paths.custom_xml_dir
    link = xml_manual.fork_mod_source(
        config.server_dir_path, key, manual_dir=manual_dir, file_id=file_id,
    )
    # One version per mission file for the whole take-over: the re-merge and
    # the apply both write the same file.
    with one_version_per_file():
        change = apply_source_change(config, key)
        # Same refusal every other write here makes: nothing of a disabled mod
        # is in the mission to replace, and a scoped merge would delete its
        # parked DISABLED wrappers. The record is written either way.
        if not change.get("applied"):
            return {**link, "applied": False, "reason": change.get("reason", "nothing to apply")}
        mission = get_current_mission(config.server_cfg_path)
        applied = xml_manual.apply_manual_file(
            config.server_dir_path, mission, link["id"], manual_dir=manual_dir,
        )
    return {**link, "applied": applied, **({} if applied else {"reason": "nothing to apply"})}


def merged_sources(config, mod: str, kind: str) -> dict:
    """Which of *mod*'s files of *kind* the last merge put into this mission.

    Read off the Mod XML report (`xml_report.build_mod_xml_report`), the one
    record of where each mod file went. ``source`` is the file when exactly one
    is merged, else None; ``sources`` lists the candidates to choose from: the
    merged files, or, when the report names none, every file of that kind the
    mod ships that is not turned off.
    """
    from app.xml.xml_report import STATUS_MERGED, build_mod_xml_report

    rows = [
        row for row in build_mod_xml_report(config)["files"]
        if row["mod"] == mod and row["kind"] == kind
    ]
    merged = [row["source"] for row in rows if row["status"] == STATUS_MERGED]
    if len(merged) == 1:
        return {"source": merged[0], "sources": merged}
    sources = merged or [row["source"] for row in rows if not row["excluded"]]
    return {"source": None, "sources": sources}
