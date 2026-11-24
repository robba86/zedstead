"""All XML merge config dicts + shared exclusion lists (consolidated from config_*.py files)."""

from __future__ import annotations

from typing import TypedDict

from app.modmgr.mods import EXCLUDED_MOD_KEYWORDS
from app.xml import xml_classify

# Shared file-part exclusion list used by xml_economy.py and the merge configs
EXCLUDED_FILE_PARTS = [
    "spawn",
    "eventspawns",
    "spawnabletypes",
    "eventgroups",
    "events",
    "territories",
    "admin",
    " ",
]

# MERGE_MODE values.
#   "children" — append the mod root's matching child elements straight into
#                the mission file's root.
#   "sections" — the file is split into named sections (cfglimitsdefinition's
#                <usageflags>/<valueflags>/…); append each mod section's
#                children into the same-named section of the mission file.
MODE_CHILDREN = "children"
MODE_SECTIONS = "sections"


class XmlMergeConfig(TypedDict):
    FILE_NAME: str
    FILE_EXT: str
    MOD_XML_ROOT_TAG: str
    MOD_XML_CHILD_TAGS: list[str]
    MOD_FILENAME_CONTAINS: str
    MISSION_FOLDER_TEMPLATE: str
    ELEMENTS_TO_REMOVE: list[str]
    EXCLUDED_FILE_PARTS: list[str]
    EXCLUDED_MOD_KEYWORDS: list[str]
    MERGE_MODE: str
    # Kind reported by xml_classify.classify_mod_xml for files that belong in
    # this merge. A file that classifies as something else is skipped even when
    # its filename matched — content wins over naming.
    CLASSIFY_KIND: str
    # True when a missing mission target is normal (not every mission ships
    # cfgrandompresets.xml) and must not be reported as a merge failure.
    OPTIONAL: bool


class CeFileConfig(TypedDict):
    """Config for a file type that can be linked via cfgeconomycore.xml."""

    filename_contains: str      # substring filter for mod XML filenames
    excluded_file_parts: list[str]  # filename parts to exclude
    root_tag: str               # expected XML root tag (for wrapping)
    ce_type: str                # type= attribute value in <file> element


def _make_merge_config(
    file_name: str,
    root_tag: str,
    child_tags: list[str],
    filename_contains: str,
    mission_template: str,
    excluded_file_parts: list[str],
    *,
    classify_kind: str,
    elements_to_remove: list[str] | None = None,
    merge_mode: str = MODE_CHILDREN,
    optional: bool = False,
) -> XmlMergeConfig:
    return XmlMergeConfig(
        FILE_NAME=file_name,
        FILE_EXT=".xml",
        MOD_XML_ROOT_TAG=root_tag,
        MOD_XML_CHILD_TAGS=child_tags,
        MOD_FILENAME_CONTAINS=filename_contains,
        MISSION_FOLDER_TEMPLATE=mission_template,
        ELEMENTS_TO_REMOVE=elements_to_remove or [],
        EXCLUDED_FILE_PARTS=excluded_file_parts,
        EXCLUDED_MOD_KEYWORDS=EXCLUDED_MOD_KEYWORDS,
        MERGE_MODE=merge_mode,
        CLASSIFY_KIND=classify_kind,
        OPTIONAL=optional,
    )


# ---------------------------------------------------------------------------
# Per-file merge configurations
# ---------------------------------------------------------------------------

TYPES_CONFIG = _make_merge_config(
    file_name="types",
    root_tag="types",
    child_tags=["type"],
    filename_contains="types",
    mission_template="mpmissions/{mission_folder}/db/",
    classify_kind=xml_classify.KIND_TYPES,
    excluded_file_parts=[
        "types",  # handled separately via cfgeconomycore
        "spawn",
        "event",
        "cfgeventspawns",
        "cfgspawnabletypes",
        "territories",
        "admin",
        " ",
    ],
)

EVENTS_CONFIG = _make_merge_config(
    file_name="events",
    root_tag="events",
    child_tags=["event"],
    filename_contains="events",
    mission_template="mpmissions/{mission_folder}/db/",
    classify_kind=xml_classify.KIND_EVENTS,
    excluded_file_parts=[
        "spawn",
        "cfgeventspawns",
        "cfgspawnabletypes",
        "types",
        "territories",
        "admin",
    ],
)

CFGEVENTGROUPS_CONFIG = _make_merge_config(
    file_name="cfgeventgroups",
    root_tag="eventgroupdef",
    child_tags=["group"],
    filename_contains="eventgroups",
    mission_template="mpmissions/{mission_folder}/",
    classify_kind=xml_classify.KIND_EVENTGROUPS,
    excluded_file_parts=[
        "spawn",
        "types",
        "_events",
        "events_",
        "types_",
        "_types",
        "territories",
        "admin",
    ],
)

CFGEVENTSPAWNS_CONFIG = _make_merge_config(
    file_name="cfgeventspawns",
    root_tag="eventposdef",
    child_tags=["event"],
    filename_contains="eventspawns",
    mission_template="mpmissions/{mission_folder}/",
    classify_kind=xml_classify.KIND_EVENTSPAWNS,
    excluded_file_parts=[
        "spawnabletypes",
        "eventgroups",
        "types_",
        "_types",
        "territories",
        "admin",
    ],
)

CFGSPAWNABLETYPES_CONFIG = _make_merge_config(
    file_name="cfgspawnabletypes",
    root_tag="spawnabletypes",
    child_tags=["type"],
    filename_contains="spawnabletypes",
    mission_template="mpmissions/{mission_folder}/",
    classify_kind=xml_classify.KIND_SPAWNABLETYPES,
    excluded_file_parts=[
        "spawns",
        "events",
        "types_",
        "_types",
        "territories",
        "admin",
    ],
)

CFGRANDOMPRESETS_CONFIG = _make_merge_config(
    file_name="cfgrandompresets",
    root_tag="randompresets",
    child_tags=["cargo", "attachments"],
    filename_contains="randompresets",
    mission_template="mpmissions/{mission_folder}/",
    classify_kind=xml_classify.KIND_RANDOMPRESETS,
    excluded_file_parts=["territories", "admin"],
    optional=True,
)

# cfglimitsdefinition*.xml are section-shaped: <lists> wrapping <categories>,
# <tags>, <usageflags>, <valueflags>. A mod that adds a new usage/value flag
# ships the same shape, so each section merges into its counterpart — appending
# at the root instead would produce a file the CE can't load.
CFGLIMITSDEFINITION_CONFIG = _make_merge_config(
    file_name="cfglimitsdefinition",
    root_tag="lists",
    child_tags=["categories", "tags", "usageflags", "valueflags"],
    filename_contains="limitsdefinition",
    mission_template="mpmissions/{mission_folder}/",
    classify_kind=xml_classify.KIND_LIMITSDEFINITION,
    excluded_file_parts=["limitsdefinitionuser", "territories", "admin"],
    merge_mode=MODE_SECTIONS,
    optional=True,
)

CFGLIMITSDEFINITIONUSER_CONFIG = _make_merge_config(
    file_name="cfglimitsdefinitionuser",
    root_tag="lists",
    child_tags=["usageflags", "valueflags"],
    filename_contains="limitsdefinitionuser",
    mission_template="mpmissions/{mission_folder}/",
    classify_kind=xml_classify.KIND_LIMITSDEFINITIONUSER,
    excluded_file_parts=["territories", "admin"],
    merge_mode=MODE_SECTIONS,
    optional=True,
)

# Filename fallback for CE-linkable files, used *only* for a well-formed XML
# fragment with no single root element — the one case content classification
# genuinely cannot answer. This is NOT the list of CE types: that is
# `xml_classify.CE_TYPES`, which has six entries. globals/economy/messages are
# deliberately absent here because their names collide with things that aren't
# them (`cfgeconomycore.xml` contains "economy"), and a rootless fragment gives
# nothing else to go on. Users needing those pick the kind explicitly through a
# manual XML file.
# The exclusion lists in each config prevent overlap (e.g. the types
# catch-all excludes filenames containing "events" or "spawnabletypes").
CE_FILE_CONFIGS: list[CeFileConfig] = [
    CeFileConfig(
        filename_contains="events",
        excluded_file_parts=EVENTS_CONFIG["EXCLUDED_FILE_PARTS"],
        root_tag=xml_classify.ce_root_tag(xml_classify.KIND_EVENTS),
        ce_type=xml_classify.KIND_EVENTS,
    ),
    CeFileConfig(
        filename_contains="spawnabletypes",
        excluded_file_parts=CFGSPAWNABLETYPES_CONFIG["EXCLUDED_FILE_PARTS"],
        root_tag=xml_classify.ce_root_tag(xml_classify.KIND_SPAWNABLETYPES),
        ce_type=xml_classify.KIND_SPAWNABLETYPES,
    ),
    CeFileConfig(
        filename_contains="",
        excluded_file_parts=EXCLUDED_FILE_PARTS,
        root_tag=xml_classify.ce_root_tag(xml_classify.KIND_TYPES),
        ce_type=xml_classify.KIND_TYPES,
    ),
]

# Direct-merge configs (no cfgeconomycore support for these file types)
ALL_MERGE_CONFIGS: dict[str, XmlMergeConfig] = {
    "cfgeventgroups": CFGEVENTGROUPS_CONFIG,
    "cfgeventspawns": CFGEVENTSPAWNS_CONFIG,
    "cfgrandompresets": CFGRANDOMPRESETS_CONFIG,
    "cfglimitsdefinition": CFGLIMITSDEFINITION_CONFIG,
    "cfglimitsdefinitionuser": CFGLIMITSDEFINITIONUSER_CONFIG,
}
