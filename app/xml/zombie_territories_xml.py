"""zombie_territories.xml parsing, multiplier application, and backup/restore."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TerritoryZone:
    """One <zone> element inside a <territory>."""
    name: str
    smin: int
    smax: int
    dmin: int
    dmax: int
    x: float
    z: float
    r: float
    element: ET.Element = field(default_factory=lambda: ET.Element("zone"))


@dataclass
class Territory:
    """One <territory> element with its zones."""
    color: str
    zones: list[TerritoryZone] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("territory"))


@dataclass
class ZombieTerritoriesFile:
    """Parsed zombie_territories.xml."""
    path: Path
    tree: ET.ElementTree
    territories: list[Territory] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_zombie_territories_path(mission_dir: Path) -> Path | None:
    """Return env/zombie_territories.xml if it exists."""
    p = mission_dir / "env" / "zombie_territories.xml"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def _count(text: str | None) -> int:
    """A spawn count as the engine reads it: `2.0` is 2, and a value that is
    not a number is 0 rather than a reason to refuse the whole file."""
    try:
        return int(text)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(text))
    except (TypeError, ValueError, OverflowError):
        return 0


def parse_zombie_territories(path: Path) -> ZombieTerritoriesFile:
    """Parse zombie_territories.xml into structured data."""
    # Comments kept: the tree is saved, and a Mod XML link's START/END MOD
    # markers are how its block is found again.
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()
    territories: list[Territory] = []

    for terr_elem in root.findall("territory"):
        color = terr_elem.get("color", "")
        zones: list[TerritoryZone] = []

        for zone_elem in terr_elem.findall("zone"):
            zones.append(TerritoryZone(
                name=zone_elem.get("name", ""),
                smin=_count(zone_elem.get("smin")),
                smax=_count(zone_elem.get("smax")),
                dmin=_count(zone_elem.get("dmin")),
                dmax=_count(zone_elem.get("dmax")),
                x=float(zone_elem.get("x", "0")),
                z=float(zone_elem.get("z", "0")),
                r=float(zone_elem.get("r", "0")),
                element=zone_elem,
            ))

        territories.append(Territory(
            color=color,
            zones=zones,
            element=terr_elem,
        ))

    return ZombieTerritoriesFile(path=path, tree=tree, territories=territories)


def save_zombie_territories(ztf: ZombieTerritoriesFile) -> None:
    """Write modified tree back to disk."""
    save_xml(ztf.tree, ztf.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_zombie_territories(path: Path) -> Path:
    """Create .backup copy (overwrites previous). Returns backup path."""
    return backup_file(path)


def restore_zombie_territories(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# Multiplier application
# ---------------------------------------------------------------------------

def apply_zone_multiplier(ztf: ZombieTerritoriesFile, multiplier: float) -> None:
    """Scale all smin/smax/dmin/dmax values by the given multiplier."""
    for territory in ztf.territories:
        for zone in territory.zones:
            zone.smin = max(0, round(zone.smin * multiplier))
            zone.smax = max(0, round(zone.smax * multiplier))
            zone.dmin = max(0, round(zone.dmin * multiplier))
            zone.dmax = max(0, round(zone.dmax * multiplier))

            zone.element.set("smin", str(zone.smin))
            zone.element.set("smax", str(zone.smax))
            zone.element.set("dmin", str(zone.dmin))
            zone.element.set("dmax", str(zone.dmax))


def count_zones(ztf: ZombieTerritoriesFile) -> int:
    """Return total number of zones across all territories."""
    return sum(len(t.zones) for t in ztf.territories)


# ---------------------------------------------------------------------------
# Generic aliases — use these for non-zombie territory files
# ---------------------------------------------------------------------------

TerritoriesFile = ZombieTerritoriesFile


def get_territories_path(mission_dir: Path, filename: str) -> Path | None:
    """Return env/{filename} if it exists."""
    p = mission_dir / "env" / filename
    return p if p.is_file() else None


def parse_territories(path: Path) -> ZombieTerritoriesFile:
    return parse_zombie_territories(path)


def save_territories(ztf: ZombieTerritoriesFile) -> None:
    return save_zombie_territories(ztf)


def backup_territories(path: Path) -> Path:
    return backup_zombie_territories(path)


def restore_territories(path: Path) -> bool:
    return restore_zombie_territories(path)
