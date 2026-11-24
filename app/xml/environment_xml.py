"""Environment XML parsing, editing, and backup/restore."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from app.xml.xml_utils import backup_file, load_xml, restore_file, save_xml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERRITORY_TYPES = ["Herd", "Ambient"]

KNOWN_BEHAVIORS: list[str] = [
    "DZWolfGroupBeh",
    "DZBearGroupBeh",
    "DZDeerGroupBeh",
    "DZBoarGroupBeh",
    "DZCattleGroupBeh",
    "DZSheepGroupBeh",
    "DZGoatGroupBeh",
    "DZHenGroupBeh",
    "DZHareGroupBeh",
    "DZFoxGroupBeh",
]

TERRITORY_DESCRIPTIONS: dict[str, str] = {
    "global_count_max": (
        "Maximum count of ALL entities spawned by this territory across the entire map. "
        "This is the global population cap — per-zone caps below are bound by it."
    ),
    "zone_count_min": (
        "Minimum entities per spawn zone (each <file> reference defines zones). "
        "Random count between zone_count_min and zone_count_max per zone, capped overall by global_count_max."
    ),
    "zone_count_max": (
        "Maximum entities per spawn zone (each <file> reference defines zones). "
        "Random count between zone_count_min and zone_count_max per zone, capped overall by global_count_max."
    ),
    # ce-ambient-spawner.wiki, "Zone cooldown (cfgenvironment.xml)".
    "zone_touch_period": (
        "Zone cooldown in seconds (zoneTouchDisableEditPeriodSec): how long a zone waits after it "
        "was deactivated before it can spawn again."
    ),
}

BEHAVIOR_DESCRIPTIONS: dict[str, str] = {
    # Vanilla-actual behaviors (used by Chernarus+, Livonia, Sakhal)
    "DZWolfGroupBeh": (
        "Wolf pack behavior. Wolves roam in groups and are aggressive toward players, "
        "attacking on sight. Packs coordinate their movement and hunting patterns."
    ),
    "DZDeerGroupBeh": (
        "Deer-class herd behavior. Used by deer, roe deer, and wild boar in vanilla. Animals travel "
        "in herds, flee from players, and provide raw meat, bones, and pelts when hunted. Boars may "
        "charge if cornered."
    ),
    "DZSheepGroupBeh": (
        "Sheep-class herd behavior. Used by sheep, goats, and pigs in vanilla. Passive grazers that "
        "stay near their territory and flee from players. Provide meat and pelts."
    ),
    "DZdomesticGroupBeh": (
        "Domestic animal/herd behavior. Used by cows in vanilla (calm, pasture-bound, non-aggressive). "
        "Animals stay near their spawn area and flee from players rather than engaging."
    ),
    "BlissBearGroupBeh": (
        "Bear behavior (vanilla Bliss variant). Bears are solitary and territorial, attacking players "
        "who enter their area. Highest damage output among wildlife."
    ),
    "DZAmbientLifeGroupBeh": (
        "Ambient life behavior. Covers standalone small animals (hens, hares, foxes) that don't travel "
        "in coordinated packs. They flee on approach and provide small amounts of meat when hunted."
    ),
    # Per-species classes — not used by vanilla maps but kept as a forward-compatibility hedge
    # in case a mod registers per-species behavior overrides.
    "DZBearGroupBeh": (
        "Bear behavior. Bears are solitary and territorial, attacking players who enter "
        "their area. They are the most dangerous wildlife with high damage output."
    ),
    "DZBoarGroupBeh": (
        "Wild boar behavior. Boars are semi-aggressive and may charge players who get "
        "too close. They roam in small groups through forested areas."
    ),
    "DZCattleGroupBeh": (
        "Cattle herd behavior. Cows are passive farm animals found near settlements. "
        "They provide large quantities of meat and fat when slaughtered."
    ),
    "DZGoatGroupBeh": (
        "Goat herd behavior. Goats are passive animals found near rural and mountainous areas. "
        "They provide a moderate amount of meat when hunted."
    ),
    "DZHenGroupBeh": (
        "Chicken flock behavior. Chickens are passive and found around villages and farms. "
        "They provide small amounts of meat and are easy to catch."
    ),
    "DZHareGroupBeh": (
        "Hare behavior. Hares are passive, fast-moving, and difficult to hunt. "
        "They are solitary and provide small amounts of meat and a pelt."
    ),
    "DZFoxGroupBeh": (
        "Fox behavior. Foxes are passive and skittish, fleeing from players on sight. "
        "They are solitary nocturnal animals providing a small amount of meat and a pelt."
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EnvFileRef:
    """A <file> reference element."""
    path: str
    usable: str
    element: ET.Element = field(default_factory=lambda: ET.Element("file"))


@dataclass
class AgentSpawn:
    """One <spawn> inside an <agent>."""
    config_name: str
    chance: str
    element: ET.Element = field(default_factory=lambda: ET.Element("spawn"))


@dataclass
class Agent:
    """One <agent> inside a <territory>."""
    type: str
    chance: str
    spawns: list[AgentSpawn] = field(default_factory=list)
    element: ET.Element = field(default_factory=lambda: ET.Element("agent"))


@dataclass
class TerritoryItem:
    """The population limits of a <territory>, "" for one it does not set.

    The game reads one ``<item name="globalCountMax" val="50" />`` per
    setting. Until 2026 Q4 zsm read and wrote all three as attributes of a
    single <item>, a form no stock file has; it is still read from an <item>
    without a name, and a write moves it to the named form.
    """
    global_count_max: str = ""
    zone_count_min: str = ""
    zone_count_max: str = ""
    zone_touch_period: str = ""


# <item name=...> -> the TerritoryItem attribute it is.
_POPULATION_ITEMS = (
    ("globalCountMax", "global_count_max"),
    ("zoneCountMin", "zone_count_min"),
    ("zoneCountMax", "zone_count_max"),
    ("zoneTouchDisableEditPeriodSec", "zone_touch_period"),
)


@dataclass
class Territory:
    """One <territory> element."""
    type: str
    name: str
    behavior: str
    file_refs: list[EnvFileRef] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    item: TerritoryItem | None = None
    element: ET.Element = field(default_factory=lambda: ET.Element("territory"))


@dataclass
class EnvironmentFile:
    """Parsed cfgenvironment.xml file."""
    path: Path
    tree: ET.ElementTree
    file_refs: list[EnvFileRef] = field(default_factory=list)
    territories: list[Territory] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_environment_path(mission_dir: Path) -> Path | None:
    """Return cfgenvironment.xml if it exists (check root + db/)."""
    for p in [
        mission_dir / "cfgenvironment.xml",
        mission_dir / "cfgEnvironment.xml",
        mission_dir / "db" / "cfgenvironment.xml",
        mission_dir / "db" / "cfgEnvironment.xml",
    ]:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def _parse_file_ref(elem: ET.Element) -> EnvFileRef:
    return EnvFileRef(
        path=elem.get("path", ""),
        usable=elem.get("usable", ""),
        element=elem,
    )


def _parse_agent(agent_elem: ET.Element) -> Agent:
    spawns: list[AgentSpawn] = []
    for spawn_elem in agent_elem.findall("spawn"):
        spawns.append(AgentSpawn(
            config_name=spawn_elem.get("configName", ""),
            chance=spawn_elem.get("chance", "0"),
            element=spawn_elem,
        ))
    return Agent(
        type=agent_elem.get("type", ""),
        chance=agent_elem.get("chance", "0"),
        spawns=spawns,
        element=agent_elem,
    )


def _named_items(terr_elem: ET.Element) -> dict[str, ET.Element]:
    return {i.get("name"): i for i in terr_elem.findall("item") if i.get("name")}


def _old_form_item(terr_elem: ET.Element) -> ET.Element | None:
    """The nameless <item> an earlier zsm wrote the three limits onto."""
    for item in terr_elem.findall("item"):
        if not item.get("name") and any(item.get(key) is not None for key, _ in _POPULATION_ITEMS):
            return item
    return None


def _parse_population(terr_elem: ET.Element) -> TerritoryItem | None:
    named = _named_items(terr_elem)
    old_form = _old_form_item(terr_elem)
    values: dict[str, str] = {}
    for key, attr in _POPULATION_ITEMS:
        if key in named:
            values[attr] = named[key].get("val", "")
        elif old_form is not None and old_form.get(key) is not None:
            values[attr] = old_form.get(key, "")
    return TerritoryItem(**values) if values else None


def parse_environment_file(path: Path) -> EnvironmentFile:
    """Parse cfgenvironment.xml."""
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    # Top-level file refs under <territories>
    file_refs: list[EnvFileRef] = []
    territories: list[Territory] = []

    terr_section = root.find("territories")
    if terr_section is not None:
        # Top-level file refs
        for file_elem in terr_section.findall("file"):
            file_refs.append(_parse_file_ref(file_elem))

        # Territories
        for terr_elem in terr_section.findall("territory"):
            t_file_refs = [_parse_file_ref(f) for f in terr_elem.findall("file")]
            t_agents = [_parse_agent(a) for a in terr_elem.findall("agent")]

            t_item = _parse_population(terr_elem)

            territories.append(Territory(
                type=terr_elem.get("type", ""),
                name=terr_elem.get("name", ""),
                behavior=terr_elem.get("behavior", ""),
                file_refs=t_file_refs,
                agents=t_agents,
                item=t_item,
                element=terr_elem,
            ))

    return EnvironmentFile(
        path=path, tree=tree, file_refs=file_refs, territories=territories,
    )


def save_environment_file(ef: EnvironmentFile) -> None:
    """Write modified tree back to disk."""
    save_xml(ef.tree, ef.path)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_environment_file(path: Path) -> Path:
    return backup_file(path)


def restore_environment_file(path: Path) -> bool:
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — territories
# ---------------------------------------------------------------------------

def add_territory(
    ef: EnvironmentFile, t_type: str, name: str, behavior: str,
) -> Territory:
    """Add a new <territory> element."""
    root = ef.tree.getroot()
    terr_section = root.find("territories")
    if terr_section is None:
        terr_section = ET.SubElement(root, "territories")

    elem = ET.SubElement(terr_section, "territory")
    elem.set("type", t_type)
    elem.set("name", name)
    elem.set("behavior", behavior)
    terr = Territory(type=t_type, name=name, behavior=behavior, element=elem)
    ef.territories.append(terr)
    return terr


def remove_territory(ef: EnvironmentFile, terr: Territory) -> None:
    """Remove a <territory> element."""
    root = ef.tree.getroot()
    terr_section = root.find("territories")
    if terr_section is not None:
        terr_section.remove(terr.element)
    ef.territories.remove(terr)


def set_territory_population(
    terr: Territory,
    global_count_max: str,
    zone_count_min: str,
    zone_count_max: str,
    zone_touch_period: str = "",
) -> None:
    """Set the population limits that differ from what the territory has.

    An empty value leaves that limit as it is, and a call that changes nothing
    writes nothing, so a territory without limits gains none.
    """
    current = terr.item or TerritoryItem()
    wanted = dict(zip((attr for _, attr in _POPULATION_ITEMS),
                      (global_count_max, zone_count_min, zone_count_max, zone_touch_period)))
    changed = {attr: v for attr, v in wanted.items() if v and v != getattr(current, attr)}
    if not changed:
        return

    named = _named_items(terr.element)
    old_form = _old_form_item(terr.element)
    for key, attr in _POPULATION_ITEMS:
        value = changed.get(attr)
        if value is None and key not in named and old_form is not None:
            value = old_form.get(key)  # carried over to the form the game reads
        if value is None:
            continue
        elem = named.get(key)
        if elem is None:
            elem = named[key] = ET.SubElement(terr.element, "item")
            elem.set("name", key)
        elem.set("val", value)

    # What an earlier zsm save left behind: the limits as attributes, which
    # would now show a second value beside the one the game reads.
    for elem in [*named.values(), *([old_form] if old_form is not None else [])]:
        for key, _ in _POPULATION_ITEMS:
            elem.attrib.pop(key, None)
    if old_form is not None and not old_form.attrib:
        terr.element.remove(old_form)

    terr.item = _parse_population(terr.element)


# ---------------------------------------------------------------------------
# CRUD — agents
# ---------------------------------------------------------------------------

def add_agent(terr: Territory, agent_type: str, chance: str) -> Agent:
    """Add an <agent> to a territory."""
    elem = ET.SubElement(terr.element, "agent")
    elem.set("type", agent_type)
    elem.set("chance", chance)
    agent = Agent(type=agent_type, chance=chance, spawns=[], element=elem)
    terr.agents.append(agent)
    return agent


def remove_agent(terr: Territory, agent: Agent) -> None:
    """Remove an <agent> from a territory."""
    terr.element.remove(agent.element)
    terr.agents.remove(agent)


def set_agent_chance(agent: Agent, chance: str) -> None:
    """Update chance on an agent."""
    agent.element.set("chance", chance)
    agent.chance = chance


# ---------------------------------------------------------------------------
# CRUD — agent spawns
# ---------------------------------------------------------------------------

def add_spawn_to_agent(agent: Agent, config_name: str, chance: str) -> AgentSpawn:
    """Add a <spawn> to an agent."""
    elem = ET.SubElement(agent.element, "spawn")
    elem.set("configName", config_name)
    elem.set("chance", chance)
    spawn = AgentSpawn(config_name=config_name, chance=chance, element=elem)
    agent.spawns.append(spawn)
    return spawn


def remove_spawn_from_agent(agent: Agent, spawn: AgentSpawn) -> None:
    """Remove a <spawn> from an agent."""
    agent.element.remove(spawn.element)
    agent.spawns.remove(spawn)
