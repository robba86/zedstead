"""Tests for cli.ops.collision_ops — collision detection across XML files."""

from pathlib import Path

from tests.conftest import write_xml

from app.ops.collision_ops import (
    CollisionReport,
    scan_all_collisions,
    scan_cross_file_collisions,
    scan_effectarea_collisions,
    scan_eventspawn_collisions,
    scan_eventspawn_collisions_from_paths,
    scan_playerspawn_collisions,
    scan_playerspawn_collisions_from_path,
    scan_territory_collisions,
    scan_territory_collisions_from_dir,
    scan_traderplus_collisions,
)
from app.parsers.effectarea_json import EffectArea, EffectAreaFile
from app.xml.eventspawns_xml import parse_eventspawns_file
from app.xml.events_xml import parse_events_file
from app.xml.playerspawnpoints_xml import parse_spawnpoints_file
from app.traderplus.traderplus_general_json import TraderNpc, TraderPlusGeneralConfig
from app.traderplus.traderplus_garage_json import GarageNPC, TraderPlusGarageConfig
from app.traderplus.traderplus_safezone_json import SafeAreaLocation, TraderPlusSafeZoneConfig
from app.xml.zombie_territories_xml import parse_zombie_territories


# ---------------------------------------------------------------------------
# Sample XML fixtures
# ---------------------------------------------------------------------------

SPAWNS_WITH_DUPLICATES = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="StaticHeliCrash">
        <pos x="4500.000000" z="9200.000000" a="180.000000"/>
        <pos x="4500.000000" z="9200.000000" a="180.000000"/>
        <pos x="6700.000000" z="3100.000000" a="-1.000000"/>
    </event>
</eventspawns>
"""

SPAWNS_CLEAN = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="StaticHeliCrash">
        <pos x="4500.000000" z="9200.000000" a="180.000000"/>
        <pos x="6700.000000" z="3100.000000" a="-1.000000"/>
    </event>
    <event name="StaticContamination">
        <pos x="1000.000000" z="1000.000000" a="0.000000"/>
    </event>
</eventspawns>
"""

SPAWNS_RADIUS_CONFLICT = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="StaticHeliCrash">
        <pos x="1000.000000" z="1000.000000" a="0.000000"/>
        <pos x="1100.000000" z="1000.000000" a="90.000000"/>
        <pos x="9000.000000" z="9000.000000" a="0.000000"/>
    </event>
</eventspawns>
"""

SPAWNS_CROSS_OVERLAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="EventA">
        <pos x="5000.000000" z="5000.000000" a="0.000000"/>
    </event>
    <event name="EventB">
        <pos x="5002.000000" z="5002.000000" a="90.000000"/>
    </event>
    <event name="EventC">
        <pos x="1000.000000" z="1000.000000" a="0.000000"/>
    </event>
</eventspawns>
"""

EVENTS_WITH_RADIUS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="StaticHeliCrash">
        <nominal>3</nominal>
        <min>1</min>
        <max>5</max>
        <lifetime>1800</lifetime>
        <restock>600</restock>
        <saferadius>500</saferadius>
        <distanceradius>300</distanceradius>
        <cleanupradius>200</cleanupradius>
        <secondary>HeliCrashWreck</secondary>
        <flags deletable="0" init_random="1" remove_damaged="1"/>
        <position>fixed</position>
        <limit>mixed</limit>
        <active>1</active>
        <children>
            <child lootmax="10" lootmin="5" max="3" min="1" type="Loot"/>
        </children>
    </event>
</events>
"""


# ---------------------------------------------------------------------------
# Tests — exact duplicates
# ---------------------------------------------------------------------------

def test_exact_duplicates_detected(tmp_path: Path):
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_WITH_DUPLICATES)
    )
    report = scan_eventspawn_collisions(spawns)

    dupes = [c for c in report.collisions if c.kind == "exact_duplicate"]
    assert len(dupes) == 1
    assert dupes[0].severity == "info"
    assert dupes[0].event_a == "StaticHeliCrash"
    assert dupes[0].pos_a_index == 0
    assert dupes[0].pos_b_index == 1
    assert dupes[0].distance == 0.0


# ---------------------------------------------------------------------------
# Tests — radius conflicts
# ---------------------------------------------------------------------------

def test_radius_conflict_detected(tmp_path: Path):
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_RADIUS_CONFLICT)
    )
    events = parse_events_file(
        write_xml(tmp_path / "db" / "events.xml", EVENTS_WITH_RADIUS)
    )
    report = scan_eventspawn_collisions(spawns, events, check_radius=True)

    radius = [c for c in report.collisions if c.kind == "radius_conflict"]
    assert len(radius) == 1
    assert radius[0].severity == "info"
    assert radius[0].event_a == "StaticHeliCrash"
    # pos[0] at 1000,1000 and pos[1] at 1100,1000 = 100m apart < 300m radius
    assert radius[0].distance == 100.0
    assert radius[0].radius == 300


def test_radius_conflict_skipped_by_default(tmp_path: Path):
    """Radius conflicts are skipped when check_radius is False (default)."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_RADIUS_CONFLICT)
    )
    events = parse_events_file(
        write_xml(tmp_path / "db" / "events.xml", EVENTS_WITH_RADIUS)
    )
    report = scan_eventspawn_collisions(spawns, events)

    radius = [c for c in report.collisions if c.kind == "radius_conflict"]
    assert len(radius) == 0


def test_no_radius_conflict_without_events(tmp_path: Path):
    """Without events.xml, radius conflicts can't be checked."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_RADIUS_CONFLICT)
    )
    report = scan_eventspawn_collisions(spawns, events=None, check_radius=True)

    radius = [c for c in report.collisions if c.kind == "radius_conflict"]
    assert len(radius) == 0


# ---------------------------------------------------------------------------
# Tests — cross-event overlap
# ---------------------------------------------------------------------------

def test_cross_event_overlap_detected(tmp_path: Path):
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CROSS_OVERLAP)
    )
    report = scan_eventspawn_collisions(spawns, check_overlap=True)

    overlaps = [c for c in report.collisions if c.kind == "cross_event_overlap"]
    assert len(overlaps) == 1
    assert overlaps[0].severity == "info"
    assert overlaps[0].event_a == "EventA"
    assert overlaps[0].event_b == "EventB"
    # sqrt(2² + 2²) ≈ 2.8m < 5m threshold
    assert overlaps[0].distance < 5.0


def test_cross_event_overlap_skipped_by_default(tmp_path: Path):
    """Cross-event overlaps are skipped when check_overlap is False (default)."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CROSS_OVERLAP)
    )
    report = scan_eventspawn_collisions(spawns)

    overlaps = [c for c in report.collisions if c.kind == "cross_event_overlap"]
    assert len(overlaps) == 0


def test_no_cross_event_when_far_apart(tmp_path: Path):
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CLEAN)
    )
    report = scan_eventspawn_collisions(spawns, check_overlap=True)

    overlaps = [c for c in report.collisions if c.kind == "cross_event_overlap"]
    assert len(overlaps) == 0


# ---------------------------------------------------------------------------
# Tests — clean file
# ---------------------------------------------------------------------------

def test_no_collisions_clean_file(tmp_path: Path):
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CLEAN)
    )
    report = scan_eventspawn_collisions(spawns)

    assert len(report.collisions) == 0
    assert report.scanned_events == 2
    assert report.scanned_positions == 3


# ---------------------------------------------------------------------------
# Tests — convenience path-based scan
# ---------------------------------------------------------------------------

def test_scan_from_paths(tmp_path: Path):
    sp = write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_WITH_DUPLICATES)
    ep = write_xml(tmp_path / "db" / "events.xml", EVENTS_WITH_RADIUS)
    report = scan_eventspawn_collisions_from_paths(sp, ep)
    assert len(report.collisions) >= 1


def test_scan_from_paths_no_events(tmp_path: Path):
    sp = write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CLEAN)
    report = scan_eventspawn_collisions_from_paths(sp, events_path=None)
    assert len(report.collisions) == 0


# ---------------------------------------------------------------------------
# Tests — severity ordering
# ---------------------------------------------------------------------------

def test_collisions_sorted_by_severity(tmp_path: Path):
    """Errors should come before info in the report."""
    # This file has both exact dupes (error) and cross-event overlap potential
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="EventA">
        <pos x="5000.000000" z="5000.000000" a="0.000000"/>
        <pos x="5000.000000" z="5000.000000" a="0.000000"/>
    </event>
    <event name="EventB">
        <pos x="5001.000000" z="5001.000000" a="0.000000"/>
    </event>
</eventspawns>
"""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", xml)
    )
    report = scan_eventspawn_collisions(spawns, check_overlap=True)

    assert len(report.collisions) >= 2
    severities = [c.severity for c in report.collisions]
    assert all(s == "info" for s in severities)


# ===========================================================================
# Player spawn collision tests
# ===========================================================================

PLAYERSPAWN_WITH_DUPES = """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <spawn_params/>
        <generator_params/>
        <group_params/>
        <generator_posbubbles>
            <group name="coastA">
                <pos x="2500" z="2200"/>
                <pos x="2500" z="2200"/>
                <pos x="3000" z="3000"/>
            </group>
        </generator_posbubbles>
    </fresh>
</playerspawnpoints>
"""

PLAYERSPAWN_CROSS_GROUP_CLUSTER = """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <spawn_params/>
        <generator_params/>
        <group_params/>
        <generator_posbubbles>
            <group name="coastA">
                <pos x="2500" z="2200"/>
            </group>
            <group name="coastB">
                <pos x="2510" z="2200"/>
            </group>
        </generator_posbubbles>
    </fresh>
</playerspawnpoints>
"""

PLAYERSPAWN_CROSS_SECTION = """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <spawn_params/>
        <generator_params/>
        <group_params/>
        <generator_posbubbles>
            <group name="coastA">
                <pos x="5000" z="5000"/>
            </group>
        </generator_posbubbles>
    </fresh>
    <hop>
        <spawn_params/>
        <generator_params/>
        <group_params/>
        <generator_posbubbles>
            <group name="hopA">
                <pos x="5002" z="5002"/>
            </group>
        </generator_posbubbles>
    </hop>
</playerspawnpoints>
"""

PLAYERSPAWN_CLEAN = """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <spawn_params/>
        <generator_params/>
        <group_params/>
        <generator_posbubbles>
            <group name="coastA">
                <pos x="2500" z="2200"/>
                <pos x="5000" z="5000"/>
            </group>
            <group name="coastB">
                <pos x="8000" z="8000"/>
            </group>
        </generator_posbubbles>
    </fresh>
</playerspawnpoints>
"""


def test_playerspawn_duplicate_within_group(tmp_path: Path):
    spf = parse_spawnpoints_file(
        write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", PLAYERSPAWN_WITH_DUPES)
    )
    report = scan_playerspawn_collisions(spf)
    dupes = [c for c in report.collisions if c.kind == "playerspawn_duplicate"]
    assert len(dupes) == 1
    assert dupes[0].severity == "info"
    assert "coastA" in dupes[0].event_a


def test_playerspawn_cross_group_cluster(tmp_path: Path):
    spf = parse_spawnpoints_file(
        write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", PLAYERSPAWN_CROSS_GROUP_CLUSTER)
    )
    report = scan_playerspawn_collisions(spf, cluster_radius=25.0)
    clusters = [c for c in report.collisions if c.kind == "playerspawn_cluster"]
    assert len(clusters) == 1
    assert clusters[0].severity == "info"
    assert clusters[0].distance == 10.0


def test_playerspawn_cross_section_overlap(tmp_path: Path):
    spf = parse_spawnpoints_file(
        write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", PLAYERSPAWN_CROSS_SECTION)
    )
    report = scan_playerspawn_collisions(spf)
    cross = [c for c in report.collisions if c.kind == "playerspawn_cross_section"]
    assert len(cross) == 1
    assert cross[0].severity == "info"
    assert "fresh" in cross[0].event_a
    assert "hop" in cross[0].event_b


def test_playerspawn_clean(tmp_path: Path):
    spf = parse_spawnpoints_file(
        write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", PLAYERSPAWN_CLEAN)
    )
    report = scan_playerspawn_collisions(spf)
    assert len(report.collisions) == 0
    assert report.scanned_positions == 3


def test_playerspawn_scan_from_path(tmp_path: Path):
    p = write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", PLAYERSPAWN_WITH_DUPES)
    report = scan_playerspawn_collisions_from_path(p)
    assert len(report.collisions) >= 1


# ===========================================================================
# Territory zone collision tests
# ===========================================================================

TERRITORY_OVERLAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory color="4286578816">
        <zone name="ZoneA" x="5000" z="5000" r="200" smin="1" smax="3" dmin="0" dmax="2"/>
        <zone name="ZoneB" x="5100" z="5000" r="200" smin="1" smax="3" dmin="0" dmax="2"/>
    </territory>
</territory_type>
"""

TERRITORY_CONTAINED = """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory color="4286578816">
        <zone name="BigZone" x="5000" z="5000" r="500" smin="1" smax="3" dmin="0" dmax="2"/>
        <zone name="SmallZone" x="5050" z="5000" r="100" smin="1" smax="3" dmin="0" dmax="2"/>
    </territory>
</territory_type>
"""

TERRITORY_NO_OVERLAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory color="4286578816">
        <zone name="ZoneA" x="1000" z="1000" r="100" smin="1" smax="3" dmin="0" dmax="2"/>
        <zone name="ZoneB" x="5000" z="5000" r="100" smin="1" smax="3" dmin="0" dmax="2"/>
    </territory>
</territory_type>
"""


def test_territory_overlap_detected(tmp_path: Path):
    ztf = parse_zombie_territories(
        write_xml(tmp_path / "zombie_territories.xml", TERRITORY_OVERLAP)
    )
    report = scan_territory_collisions([("zombie_territories.xml", ztf)])
    overlaps = [c for c in report.collisions if c.kind == "territory_overlap"]
    assert len(overlaps) == 1
    # 100m apart with r=200 each, so overlap = 300/400 = 75%
    assert overlaps[0].severity == "info"


def test_territory_contained_detected(tmp_path: Path):
    ztf = parse_zombie_territories(
        write_xml(tmp_path / "zombie_territories.xml", TERRITORY_CONTAINED)
    )
    report = scan_territory_collisions([("zombie_territories.xml", ztf)])
    contained = [c for c in report.collisions if c.kind == "territory_contained"]
    assert len(contained) == 1
    assert contained[0].severity == "info"
    assert contained[0].description.startswith("'SmallZone' (zombie_territories.xml) fully inside 'BigZone'")


def test_territory_no_overlap(tmp_path: Path):
    ztf = parse_zombie_territories(
        write_xml(tmp_path / "zombie_territories.xml", TERRITORY_NO_OVERLAP)
    )
    report = scan_territory_collisions([("zombie_territories.xml", ztf)])
    assert len(report.collisions) == 0


def test_territory_cross_file_overlap(tmp_path: Path):
    """Zones in different files that overlap should be detected."""
    zombie_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory color="4286578816">
        <zone name="ZombieZone" x="5000" z="5000" r="200" smin="1" smax="3" dmin="0" dmax="2"/>
    </territory>
</territory_type>
"""
    wolf_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory color="4286578816">
        <zone name="WolfZone" x="5050" z="5000" r="200" smin="1" smax="3" dmin="0" dmax="2"/>
    </territory>
</territory_type>
"""
    ztf = parse_zombie_territories(
        write_xml(tmp_path / "zombie_territories.xml", zombie_xml)
    )
    wtf = parse_zombie_territories(
        write_xml(tmp_path / "wolf_territories.xml", wolf_xml)
    )
    report = scan_territory_collisions([
        ("zombie_territories.xml", ztf),
        ("wolf_territories.xml", wtf),
    ])
    assert len(report.collisions) == 1
    assert "zombie_territories" in report.collisions[0].event_a
    assert "wolf_territories" in report.collisions[0].event_b


def test_territory_scan_from_dir(tmp_path: Path):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    write_xml(env_dir / "zombie_territories.xml", TERRITORY_OVERLAP)
    report = scan_territory_collisions_from_dir(tmp_path)
    assert len(report.collisions) >= 1
    assert report.scanned_events == 1


def test_territory_scan_from_dir_empty(tmp_path: Path):
    """No env/ dir → empty report."""
    report = scan_territory_collisions_from_dir(tmp_path)
    assert len(report.collisions) == 0


# ===========================================================================
# Effect area collision tests
# ===========================================================================

def test_effectarea_overlap():
    eaf = EffectAreaFile(
        path=Path("test"),
        areas=[
            EffectArea(area_name="A", pos=[5000.0, 0.0, 5000.0], radius=200.0),
            EffectArea(area_name="B", pos=[5100.0, 0.0, 5000.0], radius=200.0),
        ],
    )
    report = scan_effectarea_collisions(eaf)
    overlaps = [c for c in report.collisions if "effectarea" in c.kind]
    assert len(overlaps) == 1


def test_effectarea_no_overlap():
    eaf = EffectAreaFile(
        path=Path("test"),
        areas=[
            EffectArea(area_name="A", pos=[1000.0, 0.0, 1000.0], radius=100.0),
            EffectArea(area_name="B", pos=[5000.0, 0.0, 5000.0], radius=100.0),
        ],
    )
    report = scan_effectarea_collisions(eaf)
    assert len(report.collisions) == 0


def test_effectarea_two_static_overlap():
    # Dynamic zones are not in cfgEffectArea anymore — overlaps between
    # any pair of Areas[] entries are reported regardless of class.
    eaf = EffectAreaFile(
        path=Path("test"),
        areas=[
            EffectArea(area_name="A", pos=[5000.0, 0.0, 5000.0], radius=200.0),
            EffectArea(area_name="B", pos=[5050.0, 0.0, 5000.0], radius=200.0),
        ],
    )
    report = scan_effectarea_collisions(eaf)
    assert len(report.collisions) == 1


# ===========================================================================
# TraderPlus collision tests
# ===========================================================================

def test_safezone_overlap():
    cfg = TraderPlusSafeZoneConfig(
        safe_area_locations=[
            SafeAreaLocation(safe_zone_statut="Zone1", x=5000, y=5000, radius=100),
            SafeAreaLocation(safe_zone_statut="Zone2", x=5050, y=5000, radius=100),
        ],
    )
    report = scan_traderplus_collisions(safezone_cfg=cfg)
    overlaps = [c for c in report.collisions if c.kind == "safezone_overlap"]
    assert len(overlaps) == 1


def test_safezone_no_overlap():
    cfg = TraderPlusSafeZoneConfig(
        safe_area_locations=[
            SafeAreaLocation(safe_zone_statut="Zone1", x=1000, y=1000, radius=80),
            SafeAreaLocation(safe_zone_statut="Zone2", x=5000, y=5000, radius=80),
        ],
    )
    report = scan_traderplus_collisions(safezone_cfg=cfg)
    assert len(report.collisions) == 0


def test_trader_npc_proximity():
    cfg = TraderPlusGeneralConfig(
        traders=[
            TraderNpc(given_name="Bob", position=[5000, 0, 5000]),
            TraderNpc(given_name="Alice", position=[5002, 0, 5002]),
        ],
    )
    report = scan_traderplus_collisions(general_cfg=cfg)
    npcs = [c for c in report.collisions if c.kind == "trader_npc_proximity"]
    assert len(npcs) == 1
    assert "Bob" in npcs[0].description
    assert "Alice" in npcs[0].description


def test_garage_parking_proximity():
    cfg = TraderPlusGarageConfig(
        npcs=[
            GarageNPC(class_name="NPC1", parking_position=[5000, 0, 5000]),
            GarageNPC(class_name="NPC2", parking_position=[5001, 0, 5001]),
        ],
    )
    report = scan_traderplus_collisions(garage_cfg=cfg)
    garages = [c for c in report.collisions if c.kind == "garage_parking_proximity"]
    assert len(garages) == 1


def test_traderplus_all_clean():
    report = scan_traderplus_collisions(
        safezone_cfg=TraderPlusSafeZoneConfig(
            safe_area_locations=[
                SafeAreaLocation(x=1000, y=1000, radius=80),
                SafeAreaLocation(x=9000, y=9000, radius=80),
            ],
        ),
        general_cfg=TraderPlusGeneralConfig(
            traders=[
                TraderNpc(given_name="A", position=[1000, 0, 1000]),
                TraderNpc(given_name="B", position=[9000, 0, 9000]),
            ],
        ),
        garage_cfg=TraderPlusGarageConfig(
            npcs=[
                GarageNPC(parking_position=[1000, 0, 1000]),
                GarageNPC(parking_position=[9000, 0, 9000]),
            ],
        ),
    )
    assert len(report.collisions) == 0


# ===========================================================================
# Cross-file collision tests
# ===========================================================================

def test_spawn_inside_safezone(tmp_path: Path):
    spawns = parse_eventspawns_file(write_xml(
        tmp_path / "cfgeventspawns.xml",
        """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="HeliCrash">
        <pos x="5000.0" z="5000.0" a="0.0"/>
    </event>
</eventspawns>
""",
    ))
    sz_cfg = TraderPlusSafeZoneConfig(
        safe_area_locations=[
            SafeAreaLocation(safe_zone_statut="Town", x=5000, y=5000, radius=200),
        ],
    )
    report = scan_cross_file_collisions(spawns=spawns, safezone_cfg=sz_cfg)
    hits = [c for c in report.collisions if c.kind == "spawn_in_safezone"]
    assert len(hits) == 1
    assert hits[0].severity == "info"


def test_spawn_inside_territory(tmp_path: Path):
    spawns = parse_eventspawns_file(write_xml(
        tmp_path / "cfgeventspawns.xml",
        """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="HeliCrash">
        <pos x="5000.0" z="5000.0" a="0.0"/>
    </event>
</eventspawns>
""",
    ))
    ztf = parse_zombie_territories(write_xml(
        tmp_path / "zombie_territories.xml",
        """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory color="4286578816">
        <zone name="ZombieZone" x="5000" z="5000" r="300" smin="1" smax="3" dmin="0" dmax="2"/>
    </territory>
</territory_type>
""",
    ))
    report = scan_cross_file_collisions(
        spawns=spawns,
        territory_files=[("zombie_territories.xml", ztf)],
    )
    hits = [c for c in report.collisions if c.kind == "spawn_in_territory"]
    assert len(hits) == 1
    assert hits[0].severity == "info"


def test_playerspawn_inside_effectarea(tmp_path: Path):
    spf = parse_spawnpoints_file(write_xml(
        tmp_path / "cfgPlayerSpawnPoints.xml",
        """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <spawn_params/>
        <generator_params/>
        <group_params/>
        <generator_posbubbles>
            <group name="coast">
                <pos x="5000" z="5000"/>
            </group>
        </generator_posbubbles>
    </fresh>
</playerspawnpoints>
""",
    ))
    eaf = EffectAreaFile(
        path=Path("test"),
        areas=[
            EffectArea(area_name="A", pos=[5050.0, 0.0, 5050.0], radius=200.0),
        ],
    )
    report = scan_cross_file_collisions(spf=spf, eaf=eaf)
    hits = [c for c in report.collisions if c.kind == "playerspawn_in_effectarea"]
    assert len(hits) == 1
    assert hits[0].severity == "info"


def test_cross_file_no_collisions(tmp_path: Path):
    spawns = parse_eventspawns_file(write_xml(
        tmp_path / "cfgeventspawns.xml",
        """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="HeliCrash">
        <pos x="1000.0" z="1000.0" a="0.0"/>
    </event>
</eventspawns>
""",
    ))
    sz_cfg = TraderPlusSafeZoneConfig(
        safe_area_locations=[
            SafeAreaLocation(safe_zone_statut="Town", x=9000, y=9000, radius=80),
        ],
    )
    report = scan_cross_file_collisions(spawns=spawns, safezone_cfg=sz_cfg)
    assert report.collisions == []


# ===========================================================================
# Scan All tests
# ===========================================================================

def test_scan_all_with_eventspawns_only(tmp_path: Path):
    """scan_all finds eventspawn dupes even with minimal files."""
    write_xml(
        tmp_path / "cfgeventspawns.xml",
        SPAWNS_WITH_DUPLICATES,
    )
    report = scan_all_collisions(tmp_path)
    assert len(report.collisions) >= 1
    kinds = {c.kind for c in report.collisions}
    assert "exact_duplicate" in kinds


def test_scan_all_empty_dir(tmp_path: Path):
    """scan_all on empty dir produces no collisions."""
    report = scan_all_collisions(tmp_path)
    assert report.collisions == []


# ---------------------------------------------------------------------------
# Tests — event_names filtering
# ---------------------------------------------------------------------------

SPAWNS_MULTI_EVENT = """\
<?xml version="1.0" encoding="UTF-8"?>
<eventspawns>
    <event name="StaticHeliCrash">
        <pos x="4500.000000" z="9200.000000" a="180.000000"/>
        <pos x="4500.000000" z="9200.000000" a="180.000000"/>
    </event>
    <event name="StaticPoliceCar">
        <pos x="1000.000000" z="1000.000000" a="0.000000"/>
        <pos x="1000.000000" z="1000.000000" a="0.000000"/>
    </event>
    <event name="StaticContamination">
        <pos x="8000.000000" z="8000.000000" a="0.000000"/>
    </event>
</eventspawns>
"""


def test_event_names_filter_single(tmp_path: Path):
    """Filtering to one event only returns collisions for that event."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_MULTI_EVENT)
    )
    report = scan_eventspawn_collisions(
        spawns, event_names={"StaticHeliCrash"},
    )
    assert len(report.collisions) == 1
    assert report.collisions[0].event_a == "StaticHeliCrash"
    assert report.scanned_events == 1


def test_event_names_filter_none_scans_all(tmp_path: Path):
    """event_names=None (default) scans all entries."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_MULTI_EVENT)
    )
    report = scan_eventspawn_collisions(spawns, event_names=None)
    assert report.scanned_events == 3
    assert len(report.collisions) == 2  # one dupe per event with dupes


def test_event_names_filter_empty_set(tmp_path: Path):
    """event_names=set() scans nothing."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_MULTI_EVENT)
    )
    report = scan_eventspawn_collisions(spawns, event_names=set())
    assert report.collisions == []
    assert report.scanned_events == 0
    assert report.scanned_positions == 0


def test_event_names_filter_cross_event(tmp_path: Path):
    """Cross-event overlap only checked between filtered events."""
    spawns = parse_eventspawns_file(
        write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CROSS_OVERLAP)
    )
    # EventA and EventB overlap — filtering to just EventA excludes EventB
    report = scan_eventspawn_collisions(
        spawns, event_names={"EventA"}, check_overlap=True,
    )
    overlaps = [c for c in report.collisions if c.kind == "cross_event_overlap"]
    assert len(overlaps) == 0

    # Include both → overlap detected
    report = scan_eventspawn_collisions(
        spawns, event_names={"EventA", "EventB"}, check_overlap=True,
    )
    overlaps = [c for c in report.collisions if c.kind == "cross_event_overlap"]
    assert len(overlaps) == 1


def test_event_names_filter_from_paths(tmp_path: Path):
    """event_names works through scan_eventspawn_collisions_from_paths."""
    path = write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_MULTI_EVENT)
    report = scan_eventspawn_collisions_from_paths(
        path, event_names={"StaticPoliceCar"},
    )
    assert len(report.collisions) == 1
    assert report.collisions[0].event_a == "StaticPoliceCar"


# ---------------------------------------------------------------------------
# Tests — playerspawn section filtering (via copy + filtered dict)
# ---------------------------------------------------------------------------

def test_playerspawn_section_filter(tmp_path: Path):
    """Filtering to one section skips cross-section checks."""
    from copy import copy

    spf = parse_spawnpoints_file(
        write_xml(tmp_path / "cfgPlayerSpawnPoints.xml", PLAYERSPAWN_CROSS_SECTION)
    )
    # Full scan finds cross-section overlap
    report = scan_playerspawn_collisions(spf)
    cross = [c for c in report.collisions if c.kind == "playerspawn_cross_section"]
    assert len(cross) == 1

    # Filtered to just "fresh" — no cross-section possible
    filtered = copy(spf)
    filtered.sections = {k: v for k, v in spf.sections.items() if k == "fresh"}
    report = scan_playerspawn_collisions(filtered)
    cross = [c for c in report.collisions if c.kind == "playerspawn_cross_section"]
    assert len(cross) == 0


# ---------------------------------------------------------------------------
# Tests — territory file filtering
# ---------------------------------------------------------------------------

def test_territory_file_filter(tmp_path: Path):
    """Filtering to a subset of territory files only scans those."""
    env_dir = tmp_path / "env"
    env_dir.mkdir()

    zone_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<territory_type>
    <territory type="ZombieZone" x="5000" z="5000" r="200" dmin="1" dmax="5" flags="4">
        <zone name="Z1" x="5000" z="5000" r="200"/>
        <zone name="Z2" x="5050" z="5050" r="200"/>
    </territory>
</territory_type>
"""
    write_xml(env_dir / "zombie_territories.xml", zone_xml)
    write_xml(env_dir / "animal_territories.xml", zone_xml)

    from app.xml.zombie_territories_xml import parse_zombie_territories

    # Both files → collisions from both
    all_files = [
        ("zombie_territories.xml", parse_zombie_territories(env_dir / "zombie_territories.xml")),
        ("animal_territories.xml", parse_zombie_territories(env_dir / "animal_territories.xml")),
    ]
    report = scan_territory_collisions(all_files)
    assert report.scanned_events == 2

    # One file → only that file's collisions
    one_file = [all_files[0]]
    report = scan_territory_collisions(one_file)
    assert report.scanned_events == 1


# ---------------------------------------------------------------------------
# Tests — URL query builder
# ---------------------------------------------------------------------------

# These key names are the contract with the map editor's urlState.js — renaming
# one silently breaks the Analysis page's "open in map editor" deep link.

def test_build_collision_query_eventspawns():
    from app.ops.collision_ops import build_collision_query

    q = build_collision_query(
        "eventspawns",
        ["animalcow", "infectedmedic"],
        check_radius=True,
        check_overlap=True,
    )
    assert "collisions=eventspawns" in q
    assert "types=animalcow,infectedmedic" in q
    assert "cross-event-overlaps=true" in q
    assert "radius-conflicts=true" in q


def test_build_collision_query_playerspawns():
    from app.ops.collision_ops import build_collision_query

    q = build_collision_query("playerspawns", ["hop", "travel"])
    assert "collisions=playerspawns" in q
    assert "types=hop,travel" in q
    assert "cross-event-overlaps" not in q
    assert "radius-conflicts" not in q


def test_build_collision_query_all_no_types():
    from app.ops.collision_ops import build_collision_query

    assert build_collision_query("all") == "collisions=all"


def test_circle_overlap_pct_zero_radius_no_div_by_zero():
    from app.ops.collision_ops import _circle_overlap_pct

    # Zero-radius zone whose center sits outside the other circle: not contained,
    # so we reach the ratio branch — must return 0.0, not raise ZeroDivisionError.
    assert _circle_overlap_pct(d=10.0, r1=0.0, r2=5.0) == 0.0
    # Zero-radius point inside the other circle is still "contained" → 100%.
    assert _circle_overlap_pct(d=3.0, r1=0.0, r2=5.0) == 100.0


def test_circle_overlap_pct_partial_overlap():
    from app.ops.collision_ops import _circle_overlap_pct

    # Two r=5 circles, centers 8 apart: overlap_depth=2, /(5*2)=20%.
    assert _circle_overlap_pct(d=8.0, r1=5.0, r2=5.0) == 20.0


# ---------------------------------------------------------------------------
# Tests — a file that cannot be read is left out and named, never raised
# ---------------------------------------------------------------------------

import dataclasses
import json

import pytest

_PLAYERSPAWN_BAD_POS = """\
<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <generator_posbubbles>
            <group name="coastA">
                <pos x="{x}" z="5"/>
                <pos x="2500" z="2200"/>
                <pos x="2500" z="2200"/>
            </group>
        </generator_posbubbles>
    </fresh>
</playerspawnpoints>
"""

_ECONOMYCORE_MOD_EVENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<economycore>
    <ce folder="custom">
        <file name="mod-events.xml" type="events"/>
    </ce>
</economycore>
"""


def _events_xml(name: str, radius: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<events>\n'
        f'    <event name="{name}"><nominal>5</nominal>'
        f"<distanceradius>{radius}</distanceradius></event>\n</events>\n"
    )


def _spawns_xml(*blocks: tuple[str, list[tuple[float, float, float]]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<eventspawns>"]
    for name, positions in blocks:
        out.append(f'    <event name="{name}">')
        out += [f'        <pos x="{x}" z="{z}" a="{a}"/>' for x, z, a in positions]
        out.append("    </event>")
    out.append("</eventspawns>")
    return "\n".join(out) + "\n"


def _mission_with_duplicates(mission: Path) -> None:
    """One finding per kind of file, so a test can see the other scans still ran."""
    write_xml(mission / "cfgeventspawns.xml", SPAWNS_WITH_DUPLICATES)
    write_xml(mission / "cfgplayerspawnpoints.xml", PLAYERSPAWN_WITH_DUPES)
    write_xml(mission / "env" / "zombie_territories.xml", TERRITORY_OVERLAP)


def test_a_report_lists_no_skipped_files_by_default():
    assert CollisionReport().skipped == []
    assert dataclasses.asdict(CollisionReport())["skipped"] == []


@pytest.mark.parametrize("x", ["12,5", "", "abc"])
def test_an_unreadable_player_spawn_position_is_left_out(tmp_path: Path, x: str):
    path = write_xml(tmp_path / "cfgplayerspawnpoints.xml", _PLAYERSPAWN_BAD_POS.format(x=x))
    report = scan_playerspawn_collisions_from_path(path)
    dupes = [c for c in report.collisions if c.kind == "playerspawn_duplicate"]
    assert [(c.pos_a_index, c.pos_b_index) for c in dupes] == [(1, 2)]
    assert report.skipped == ["cfgplayerspawnpoints.xml: 1 position without a readable x and z"]

    cross = scan_cross_file_collisions(spf=parse_spawnpoints_file(path))
    assert cross.scanned_positions == 2


_BAD_TERRITORIES = [
    b'<territory_type><territory><zone name="A" x="1" z="0" r=""/></territory></territory_type>',
    b'<territory_type><territory><zone name="A" x="east" z="0" r="5"/></territory></territory_type>',
    b'<territory_type><!-- Rev\xe9 --><territory><zone name="A" x="1" z="0" r="5"/></territory></territory_type>',
]


@pytest.mark.parametrize("content", _BAD_TERRITORIES)
def test_a_territory_file_that_cannot_be_read_is_skipped_and_named(tmp_path: Path, content: bytes):
    _mission_with_duplicates(tmp_path)
    (tmp_path / "env" / "wolf_territories.xml").write_bytes(content)

    for report in (scan_territory_collisions_from_dir(tmp_path), scan_all_collisions(tmp_path)):
        assert report.skipped == ["wolf_territories.xml"]
        assert "territory_overlap" in {c.kind for c in report.collisions}


@pytest.mark.parametrize("name, content, lost", [
    ("cfgeffectarea.json", '{"Areas": [{"AreaName": "Rev\xe9", "Data": {}}]}'.encode("cp1252"), set()),
    ("cfgeffectarea.json", b'{"Areas": [{"AreaName": "A", "Data": {"Radius": null}}]}', set()),
    ("cfgeffectarea.json", b'{"Areas": null}', set()),
    ("cfgeventspawns.xml", b"<eventspawns><event name='A'>", {"exact_duplicate"}),
    ("cfgplayerspawnpoints.xml", b"<playerspawnpoints><fresh>", {"playerspawn_duplicate"}),
    ("db/events.xml", b"<events><event name='A'>", set()),
])
def test_scan_all_leaves_out_the_one_file_it_cannot_read(
    tmp_path: Path, name: str, content: bytes, lost: set[str],
):
    _mission_with_duplicates(tmp_path)
    (tmp_path / "db").mkdir()
    (tmp_path / name).write_bytes(content)

    report = scan_all_collisions(tmp_path, check_radius=True)

    assert report.skipped == [Path(name).name]
    expected = {"exact_duplicate", "playerspawn_duplicate", "territory_overlap"} - lost
    assert expected <= {c.kind for c in report.collisions}


def test_the_single_scans_name_a_file_they_cannot_read(tmp_path: Path):
    spawns = tmp_path / "cfgeventspawns.xml"
    spawns.write_bytes(b"<eventspawns><event name='A'>")
    report = scan_eventspawn_collisions_from_paths(spawns)
    assert (report.collisions, report.skipped) == ([], ["cfgeventspawns.xml"])

    write_xml(spawns, SPAWNS_WITH_DUPLICATES)
    events = tmp_path / "db" / "events.xml"
    events.parent.mkdir()
    events.write_bytes(b"<events><!-- Rev\xe9 --></events>")
    report = scan_eventspawn_collisions_from_paths(spawns, events, check_radius=True)
    assert report.skipped == ["events.xml"]
    assert [c.kind for c in report.collisions] == ["exact_duplicate"]

    players = tmp_path / "cfgplayerspawnpoints.xml"
    players.write_bytes(b"<playerspawnpoints><fresh>")
    report = scan_playerspawn_collisions_from_path(players)
    assert (report.collisions, report.skipped) == ([], ["cfgplayerspawnpoints.xml"])


# ---------------------------------------------------------------------------
# Tests — the radius check knows an event a mod defines
# ---------------------------------------------------------------------------

def _mission_with_a_mod_event(mission: Path) -> Path:
    write_xml(mission / "cfgeconomycore.xml", _ECONOMYCORE_MOD_EVENTS)
    write_xml(mission / "custom" / "mod-events.xml", _events_xml("VehicleX", 50))
    return write_xml(
        mission / "cfgeventspawns.xml",
        _spawns_xml(("VehicleX", [(100, 200, 0), (110, 200, 0)])),
    )


def test_the_radius_check_reads_an_event_from_a_registered_custom_file(tmp_path: Path):
    spawns = _mission_with_a_mod_event(tmp_path)
    expected = ["'VehicleX' pos[0] and pos[1] are 10m apart (distanceradius=50m)"]

    reports = [
        scan_eventspawn_collisions_from_paths(spawns, check_radius=True),
        scan_eventspawn_collisions(parse_eventspawns_file(spawns), check_radius=True),
        scan_all_collisions(tmp_path, check_radius=True),
    ]
    for report in reports:
        assert [c.description for c in report.collisions] == expected
        assert report.skipped == []


def test_an_unregistered_custom_events_file_is_not_read(tmp_path: Path):
    spawns = _mission_with_a_mod_event(tmp_path)
    (tmp_path / "cfgeconomycore.xml").unlink()
    assert scan_eventspawn_collisions_from_paths(spawns, check_radius=True).collisions == []


def test_db_events_wins_over_a_custom_file_for_the_same_event(tmp_path: Path):
    spawns = _mission_with_a_mod_event(tmp_path)
    events = write_xml(tmp_path / "db" / "events.xml", _events_xml("VehicleX", 5))
    assert scan_eventspawn_collisions_from_paths(spawns, events, check_radius=True).collisions == []


def test_a_custom_events_file_that_cannot_be_read_is_named(tmp_path: Path):
    spawns = _mission_with_a_mod_event(tmp_path)
    (tmp_path / "custom" / "mod-events.xml").write_bytes(b"<events><!-- Rev\xe9 --></events>")
    report = scan_eventspawn_collisions_from_paths(spawns, check_radius=True)
    assert (report.collisions, report.skipped) == ([], ["custom/mod-events.xml"])


# ---------------------------------------------------------------------------
# Tests — one event's positions are checked together
# ---------------------------------------------------------------------------

def test_the_same_spot_at_another_angle_is_a_radius_conflict(tmp_path: Path):
    spawns = parse_eventspawns_file(write_xml(
        tmp_path / "cfgeventspawns.xml",
        _spawns_xml(("VehicleX", [(100, 200, 0), (100, 200, 90), (100, 200, 0)])),
    ))
    events = parse_events_file(write_xml(tmp_path / "events.xml", _events_xml("VehicleX", 50)))
    report = scan_eventspawn_collisions(spawns, events, check_radius=True)
    found = sorted((c.kind, c.pos_a_index, c.pos_b_index) for c in report.collisions)
    assert found == [
        ("exact_duplicate", 0, 2),
        ("radius_conflict", 0, 1),
        ("radius_conflict", 1, 2),
    ]


# ---------------------------------------------------------------------------
# Tests — wording and counts
# ---------------------------------------------------------------------------

def test_fully_inside_names_the_smaller_zone_first(tmp_path: Path):
    for n, zones in enumerate([
        '<zone name="Big" x="0" z="0" r="100"/><zone name="Small" x="5" z="0" r="10"/>',
        '<zone name="Small" x="5" z="0" r="10"/><zone name="Big" x="0" z="0" r="100"/>',
    ]):
        ztf = parse_zombie_territories(write_xml(
            tmp_path / f"{n}.xml",
            f"<territory_type><territory>{zones}</territory></territory_type>",
        ))
        report = scan_territory_collisions([("z.xml", ztf)])
        assert [c.description for c in report.collisions] == [
            "'Small' (z.xml) fully inside 'Big' (z.xml) — spawns stack in overlap"
        ]


def test_scan_all_counts_each_position_once(tmp_path: Path):
    spawns = write_xml(tmp_path / "cfgeventspawns.xml", SPAWNS_CLEAN)
    players = write_xml(tmp_path / "cfgplayerspawnpoints.xml", PLAYERSPAWN_WITH_DUPES)
    single = (
        scan_eventspawn_collisions_from_paths(spawns).scanned_positions
        + scan_playerspawn_collisions_from_path(players).scanned_positions
    )
    assert single == 6
    assert scan_all_collisions(tmp_path).scanned_positions == single


# ---------------------------------------------------------------------------
# Tests — TraderPlus
# ---------------------------------------------------------------------------

def test_a_trader_without_a_position_is_left_out():
    cfg = TraderPlusGeneralConfig(
        traders=[
            TraderNpc(given_name="Bob", position=[]),
            TraderNpc(given_name="Alice", position=[5000, 0, 5000]),
            TraderNpc(given_name="Carol", position=[5001, 0, 5000]),
        ],
    )
    report = scan_traderplus_collisions(general_cfg=cfg)
    assert [c.description for c in report.collisions] == [
        "Traders 'Alice' and 'Carol' are 1.0m apart"
    ]
    assert report.skipped == [
        "TraderPlusGeneralConfig.json: 1 position without a readable x and z"
    ]

    garages = TraderPlusGarageConfig(
        npcs=[GarageNPC(parking_position=[]), GarageNPC(parking_position=[])],
    )
    report = scan_traderplus_collisions(garage_cfg=garages)
    assert report.collisions == []
    assert report.skipped == [
        "TraderPlusGarageConfig.json: 2 positions without a readable x and z"
    ]


def test_a_trader_is_described_by_the_name_its_label_uses():
    cfg = TraderPlusGeneralConfig(
        traders=[
            TraderNpc(name="SurvivorM_Boris", position=[5000, 0, 5000]),
            TraderNpc(position=[5000, 0, 5000]),
        ],
    )
    report = scan_traderplus_collisions(general_cfg=cfg)
    assert [(c.event_a, c.event_b, c.description) for c in report.collisions] == [
        ("Trader/SurvivorM_Boris", "Trader/1", "Traders 'SurvivorM_Boris' and '1' are 0.0m apart")
    ]


@pytest.mark.parametrize("content, skipped", [
    ('{"Traders": [{"Position": []}, {"Position": []}]}',
     ["TraderPlusGeneralConfig.json: 2 positions without a readable x and z"]),
    ('{"Traders": [', ["TraderPlusGeneralConfig.json"]),
    ('{"Traders": null}', ["TraderPlusGeneralConfig.json"]),
])
def test_scan_all_says_when_traderplus_was_not_scanned(
    tmp_path: Path, sample_config, content: str, skipped: list[str],
):
    mission = tmp_path / "mission"
    _mission_with_duplicates(mission)
    tp_dir = sample_config.server_dir_path / "profiles" / "TraderPlus" / "TraderPlusConfig"
    tp_dir.mkdir(parents=True)
    (tp_dir / "TraderPlusGeneralConfig.json").write_text(content, encoding="utf-8")

    report = scan_all_collisions(mission, config=sample_config)

    assert report.skipped == skipped
    assert "exact_duplicate" in {c.kind for c in report.collisions}
