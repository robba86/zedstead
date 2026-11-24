"""Classname suggestion sources for the spawn editor.

Provides classname suggestions for the Create Event dialog from multiple sources:
- types.xml files in the mission directory
- Loose XML files shipped with @mod folders
- CfgVehicles classes extracted from PBO files
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from app.xml.types_xml import parse_types_file, scan_types_files

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache — avoid re-parsing files that haven't changed
# ---------------------------------------------------------------------------

_types_cache: dict[Path, tuple[float, list[dict]]] = {}


# ---------------------------------------------------------------------------
# Source 1: types.xml classnames
# ---------------------------------------------------------------------------

def get_types_xml_names(mission_dir: Path) -> dict:
    """Return classnames from mission types.xml files.

    Returns a source dict: {"id": "types", "label": ..., "names": [...]}.
    """
    names: list[dict] = []
    seen: set[str] = set()

    for path in scan_types_files(mission_dir):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue

        # Use cached result if file unchanged
        cached = _types_cache.get(path)
        if cached and cached[0] == mtime:
            file_names = cached[1]
        else:
            file_names = _parse_type_names(path, mission_dir)
            _types_cache[path] = (mtime, file_names)

        for entry in file_names:
            key = entry["name"].lower()
            if key not in seen:
                seen.add(key)
                names.append(entry)

    return {"id": "types", "label": "Mission types.xml", "names": names}


def _parse_type_names(path: Path, mission_dir: Path) -> list[dict]:
    """Parse a types XML file and return name+detail entries."""
    try:
        types_file = parse_types_file(path)
    except (OSError, ET.ParseError, ValueError):  # ValueError: not UTF-8
        log.warning("Failed to parse types file: %s", path)
        return []

    try:
        rel = path.relative_to(mission_dir)
    except ValueError:
        rel = path.name

    entries: list[dict] = []
    for te in types_file.entries:
        if not te.name:
            continue
        detail = str(rel)
        if te.category:
            detail = f"{te.category} | {rel}"
        entries.append({"name": te.name, "detail": detail})
    return entries


# ---------------------------------------------------------------------------
# Source 2: Mod folder XML files
# ---------------------------------------------------------------------------

_mod_xml_cache: dict[str, tuple[float, list[dict]]] = {}


def get_mod_xml_names(server_dir: Path) -> dict:
    """Scan @mod folders for loose XML files with <type> or <event> elements.

    Returns a source dict: {"id": "mod_xml", "label": ..., "names": [...]}.
    """
    names: list[dict] = []
    seen: set[str] = set()

    try:
        mod_dirs = sorted(server_dir.glob("@*/"))
    except OSError:
        return {"id": "mod_xml", "label": "Mod XML files", "names": []}

    for mod_dir in mod_dirs:
        if not mod_dir.is_dir():
            continue
        mod_name = mod_dir.name

        # Use max mtime of XML files (directory mtime may not update
        # when individual files inside change).
        try:
            xml_mtimes = [f.stat().st_mtime for f in mod_dir.rglob("*.xml") if f.is_file()]
            mtime = max(xml_mtimes) if xml_mtimes else mod_dir.stat().st_mtime
        except OSError:
            continue

        cached = _mod_xml_cache.get(mod_name)
        if cached and cached[0] == mtime:
            mod_names = cached[1]
        else:
            mod_names = _scan_mod_xml_names(mod_dir)
            _mod_xml_cache[mod_name] = (mtime, mod_names)

        for entry in mod_names:
            key = entry["name"].lower()
            if key not in seen:
                seen.add(key)
                names.append(entry)

    return {"id": "mod_xml", "label": "Mod XML files", "names": names}


def _scan_mod_xml_names(mod_dir: Path) -> list[dict]:
    """Find XML files in a mod dir and extract type/event names."""
    from xml.etree import ElementTree as ET

    from app.xml.xml_utils import load_xml

    entries: list[dict] = []
    mod_name = mod_dir.name

    # Search common locations for XML files
    xml_files: list[Path] = []
    for pattern in ("*.xml", "**/*.xml"):
        try:
            xml_files.extend(mod_dir.glob(pattern))
        except OSError:
            continue
        # Only do recursive search if no top-level XMLs found
        if xml_files:
            break

    # Deduplicate and skip addons/ (PBO territory)
    seen_paths: set[Path] = set()
    for xml_path in xml_files:
        if xml_path in seen_paths:
            continue
        seen_paths.add(xml_path)

        # Skip files inside addons/ (those are PBO-related)
        try:
            rel = xml_path.relative_to(mod_dir)
            if rel.parts and rel.parts[0].lower() == "addons":
                continue
        except ValueError:
            continue

        try:
            tree = load_xml(xml_path)
        except (OSError, ET.ParseError, ValueError):
            # Any mod can ship a file that is not XML, not UTF-8 (a
            # ValueError) or a folder named *.xml; the rest still count.
            continue

        root = tree.getroot()
        detail = f"{mod_name}/{rel}"

        # Extract <type name="..."> entries
        for elem in root.iter("type"):
            name = elem.get("name", "").strip()
            if name:
                entries.append({"name": name, "detail": detail})

        # Extract <event name="..."> entries
        for elem in root.iter("event"):
            name = elem.get("name", "").strip()
            if name:
                entries.append({"name": name, "detail": detail})

    return entries


# ---------------------------------------------------------------------------
# Source 3: PBO CfgVehicles classes
# ---------------------------------------------------------------------------

def get_pbo_class_names(config: DayzConfig) -> dict:
    """Extract CfgVehicles classnames from mod PBOs.

    Returns a source dict: {"id": "pbo", "label": ..., "names": [...]}.
    """
    from app.modmgr.mod_conflict import extract_mod_classnames_cached
    from app.modmgr import modlist

    names: list[dict] = []
    seen: set[str] = set()
    server_dir = config.server_dir_path

    try:
        mod_list = [m for m in modlist.installed(config)]
    except OSError:
        return {"id": "pbo", "label": "PBO classes", "names": []}

    try:
        mod_classes = extract_mod_classnames_cached(server_dir, mod_list)
    except Exception:
        log.warning("Failed to extract PBO classnames")
        return {"id": "pbo", "label": "PBO classes", "names": []}

    for mod_name, classes in sorted(mod_classes.items()):
        for cls in classes:
            key = cls.lower()
            if key not in seen:
                seen.add(key)
                names.append({"name": cls, "detail": f"{mod_name} (CfgVehicles)"})

    return {"id": "pbo", "label": "PBO classes", "names": names}
