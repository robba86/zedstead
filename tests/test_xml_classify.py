"""Content classification + map-variant filtering (app/xml/xml_classify.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.xml import xml_classify as xc
from tests.conftest import write_xml

# A mod's spawn-position table. Root tag is <events>, NOT <eventposdef> — this
# is what the Bicycles mod ships, and what the old root-tag-keyed merge dropped.
MOD_EVENTSPAWNS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="VehicleHunterz_Old_Bike">
        <pos x="11506" y="220.9" z="7503.6" a="177.8"/>
    </event>
</events>"""

MOD_ECONOMY_EVENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="VehicleHunterz_Old_Bike">
        <nominal>20</nominal>
        <lifetime>300</lifetime>
        <children><child type="Old_Bike"/></children>
    </event>
</events>"""


@pytest.mark.parametrize(
    "name,text,expected",
    [
        ("cfgeventspawns_Chernarus.xml", MOD_EVENTSPAWNS, xc.KIND_EVENTSPAWNS),
        ("events.xml", MOD_ECONOMY_EVENTS, xc.KIND_EVENTS),
        ("types.xml", "<types><type name='X'/></types>", xc.KIND_TYPES),
        (
            "spawnabletypes.xml",
            "<spawnabletypes><type name='X'/></spawnabletypes>",
            xc.KIND_SPAWNABLETYPES,
        ),
        (
            "cfgeventspawns.xml",
            "<eventposdef><event name='X'><pos x='1' z='2' a='0'/></event></eventposdef>",
            xc.KIND_EVENTSPAWNS,
        ),
        (
            "cfgeventgroups.xml",
            "<eventgroupdef><group name='X'/></eventgroupdef>",
            xc.KIND_EVENTGROUPS,
        ),
        (
            "cfgrandompresets.xml",
            "<randompresets><cargo name='X' chance='1'/></randompresets>",
            xc.KIND_RANDOMPRESETS,
        ),
        (
            "cfglimitsdefinitionuser.xml",
            "<lists><usageflags><user name='X'><usage name='Town'/></user></usageflags></lists>",
            xc.KIND_LIMITSDEFINITIONUSER,
        ),
        (
            "cfglimitsdefinition.xml",
            "<lists><usageflags><usage name='Town'/></usageflags></lists>",
            xc.KIND_LIMITSDEFINITION,
        ),
        (
            "globals.xml",
            "<variables><var name='ZombieMaxCount' type='0' value='800'/></variables>",
            xc.KIND_GLOBALS,
        ),
        (
            "economy.xml",
            "<economy><dynamic init='1' load='0' respawn='1' save='0'/></economy>",
            xc.KIND_ECONOMY,
        ),
        (
            "messages.xml",
            "<messages><message><text>hi</text></message></messages>",
            xc.KIND_MESSAGES,
        ),
        # Mod-private settings file: recognisably not a CE file.
        ("mod_settings.xml", "<mysettings><foo/></mysettings>", None),
        # <variables> without <var> children is somebody else's settings file.
        ("settings.xml", "<variables><setting name='x'/></variables>", None),
        # A trader mod's own <economy> — no CE system flags, so not economy.xml.
        ("prices.xml", "<economy><item name='X' price='5'/></economy>", None),
        # <globals> is not a DayZ root tag (the real one is <variables>). Mapping
        # it would make the CE copy wrap it *again* and register nonsense.
        ("globals.xml", "<globals><var name='X' type='0' value='1'/></globals>", None),
        # A mod shipping its own cfgeconomycore must never read as economy.xml.
        ("cfgeconomycore.xml", "<economycore><ce folder='db'/></economycore>", None),
    ],
)
def test_classify_mod_xml(tmp_path: Path, name, text, expected):
    path = write_xml(tmp_path / name, text)
    assert xc.classify_mod_xml(path) == expected


def test_classify_unparseable_returns_none(tmp_path: Path):
    """Bare fragments (no single root) can't be parsed until wrapped — callers
    fall back to filename bucketing rather than dropping the file."""
    path = tmp_path / "fragment.xml"
    path.write_text("<type name='A'/>\n<type name='B'/>\n", encoding="utf-8")
    assert xc.classify_mod_xml(path) is None


def test_classify_mod_file_reports_the_root_tag(tmp_path: Path):
    """`classify_mod_xml`'s bare None collapses two cases the merge must keep
    apart: a rootless fragment (guess from the filename) and a file that parses
    with a root the CE doesn't know (not a CE file at all)."""
    rooted = write_xml(tmp_path / "mod_settings.xml", "<mysettings><foo/></mysettings>")
    assert xc.classify_mod_file(rooted) == (None, "mysettings")

    known = write_xml(tmp_path / "types.xml", "<types><type name='X'/></types>")
    assert xc.classify_mod_file(known) == (xc.KIND_TYPES, "types")

    fragment = tmp_path / "fragment.xml"
    fragment.write_text("<type name='A'/>\n<type name='B'/>\n", encoding="utf-8")
    assert xc.classify_mod_file(fragment) == (None, None)

    broken = tmp_path / "broken.xml"
    broken.write_text("<types><type", encoding="utf-8")
    assert xc.classify_mod_file(broken) == (None, None)


# ---------------------------------------------------------------------------
# The six CE types
# ---------------------------------------------------------------------------


def test_ce_types_are_the_six_dayz_accepts():
    """DayZ's cfgeconomycore `<file type=...>` vocabulary, verbatim."""
    assert set(xc.CE_TYPES) == {
        "types", "spawnabletypes", "globals", "economy", "events", "messages",
    }
    assert xc.CE_KINDS == frozenset(xc.CE_TYPES)
    assert set(xc.CE_ROOT_TAGS) == xc.CE_KINDS


def test_messages_is_understood_but_never_auto_linked():
    """A mod ships example mission files as documentation and the merge rglobs
    the whole mod dir, so auto-linking one would broadcast its sample messages
    to real players. The admin opts in with "Set as manual"."""
    assert xc.AUTO_LINK_KINDS == xc.CE_KINDS - {xc.KIND_MESSAGES}


def test_ce_type_order_covers_every_type():
    assert set(xc.CE_TYPE_ORDER) == xc.CE_KINDS
    order = xc.CE_TYPE_ORDER
    # Settings before content; types before what references it; messages last
    # (it is append-only, so its position carries no override meaning).
    assert order["economy"] < order["globals"] < order["types"]
    assert order["types"] < order["spawnabletypes"] < order["events"] < order["messages"]


@pytest.mark.parametrize("kind", sorted(xc.CE_KINDS))
def test_ce_root_tags_round_trip(tmp_path: Path, kind):
    """A minimal document built from CE_ROOT_TAGS classifies back to its kind.

    `_ROOT_TAG_KINDS` is deliberately NOT seeded from `CE_ROOT_TAGS` (events has
    to keep going through `_classify_events_like`, and variables/economy need
    their content guards), so this is what keeps the two from drifting.
    """
    bodies = {
        "types": "<type name='X'><nominal>1</nominal></type>",
        "spawnabletypes": "<type name='X'/>",
        "events": "<event name='E'><nominal>1</nominal></event>",
        "globals": "<var name='X' type='0' value='1'/>",
        "economy": "<dynamic init='1' load='0' respawn='1' save='0'/>",
        "messages": "<message><text>hi</text></message>",
    }
    root = xc.ce_root_tag(kind)
    path = write_xml(tmp_path / f"{kind}.xml", f"<{root}>{bodies[kind]}</{root}>")
    assert xc.classify_mod_xml(path) == kind


def test_terrain_from_mission():
    assert xc.terrain_from_mission("dayzOffline.chernarusplus") == "chernarusplus"
    assert xc.terrain_from_mission("empty.Bitterroot") == "bitterroot"


@pytest.mark.parametrize(
    "filename,terrain,expected",
    [
        ("cfgeventspawns_Chernarus.xml", "chernarusplus", True),
        ("cfgeventspawns_Livonia.xml", "chernarusplus", False),
        ("cfgeventspawns_Namalsk.xml", "chernarusplus", False),
        ("cfgeventspawns_Livonia.xml", "enoch", True),
        # No map marker at all -> always applies.
        ("events.xml", "chernarusplus", True),
        # banov is a substring of banovfrost: the matching token must win.
        ("types_BanovFrost.xml", "banovfrost", True),
        ("types_Banov.xml", "banovfrost", False),
        # ...in both directions: a Banov Frost file is not Banov's.
        ("types_banovfrost.xml", "banov", False),
        ("types_Banov_Frost.xml", "banov", False),
        ("types_Banov.xml", "banov", True),
        # Unknown terrain: fall back to the terrain string itself.
        ("types_Weirdmap.xml", "weirdmap", True),
        # A short map name inside a longer word is not a map marker.
        ("Parachutes_types.xml", "chernarusplus", True),
        ("PatrolRoutes_events.xml", "banov", True),
        ("item_attributes_types.xml", "enoch", True),
        ("Sweden_Clothes_types.xml", "chernarusplus", True),
        # ...and still one as a word of its own.
        ("types_Utes.xml", "chernarusplus", False),
        ("EdenTypes.xml", "chernarusplus", False),
        ("cfgeventspawns-eden.xml", "enoch", False),
        # A long map name is found however the file glues it.
        ("typeslivonia.xml", "chernarusplus", False),
        ("typeslivonia.xml", "enoch", True),
    ],
)
def test_file_matches_terrain(filename, terrain, expected):
    assert xc.file_matches_terrain(filename, terrain) is expected


def test_file_matches_terrain_without_terrain_is_permissive():
    assert xc.file_matches_terrain("cfgeventspawns_Livonia.xml", None) is True


# ---------------------------------------------------------------------------
# classify_xml_text — the paste box's classifier
# ---------------------------------------------------------------------------
#
# Same decisions classify_mod_xml makes, but over a string that is usually a
# bare fragment: people copy the elements out of a file, not the file.


@pytest.mark.parametrize(
    "content,expected",
    [
        # Rooted files classify exactly as the on-disk ones do.
        ("<types><type name='X'><nominal>1</nominal></type></types>", xc.KIND_TYPES),
        ("<eventposdef><event name='E'><pos x='1' z='2' a='0'/></event></eventposdef>",
         xc.KIND_EVENTSPAWNS),
        ("<events><event name='E'><nominal>1</nominal></event></events>", xc.KIND_EVENTS),
        ("<spawnabletypes><type name='X'/></spawnabletypes>", xc.KIND_SPAWNABLETYPES),
        ("<eventgroupdef><group name='G'/></eventgroupdef>", xc.KIND_EVENTGROUPS),
        ("<variables><var name='X' type='0' value='1'/></variables>", xc.KIND_GLOBALS),
        ("<economy><dynamic init='1' load='0' respawn='1' save='0'/></economy>",
         xc.KIND_ECONOMY),
        ("<messages><message><text>hi</text></message></messages>", xc.KIND_MESSAGES),
        # Rootless fragments — what the manual-link form actually receives.
        ("<type name='X'><nominal>1</nominal></type><type name='Y'/>", xc.KIND_TYPES),
        ("<type name='X'><attachments><item name='A'/></attachments></type>",
         xc.KIND_SPAWNABLETYPES),
        ("<event name='E'><pos x='1' z='2' a='0'/></event>", xc.KIND_EVENTSPAWNS),
        ("<event name='E'><nominal>1</nominal></event>", xc.KIND_EVENTS),
        ("<group name='G'><child type='X'/></group>", xc.KIND_EVENTGROUPS),
        ("<cargo name='C'/>", xc.KIND_RANDOMPRESETS),
        ("<usageflags><value name='Town'/></usageflags>", xc.KIND_LIMITSDEFINITION),
        ("<usageflags><user name='Mine'><usage name='Town'/></user></usageflags>",
         xc.KIND_LIMITSDEFINITIONUSER),
        ("<var name='X' type='0' value='1'/>", xc.KIND_GLOBALS),
        ("<message><text>hi</text></message>", xc.KIND_MESSAGES),
        # Economy systems are named by their *tag*, so only the flags identify them.
        ("<dynamic init='1' load='0' respawn='1' save='0'/><animals init='1'/>",
         xc.KIND_ECONOMY),
        # Nothing recognisable, and nothing parseable.
        ("<variables><foo/></variables>", None),   # <var> is what makes it globals
        ("<economy><item price='5'/></economy>", None),  # a trader mod's own <economy>
        ("<type name='X'", None),
        ("   ", None),
    ],
)
def test_classify_xml_text(content, expected):
    assert xc.classify_xml_text(content) is expected


def test_bohemias_user_lists_root_is_the_user_limits_file(tmp_path):
    """The stock cfglimitsdefinitionuser.xml's root is `<user_lists>`."""
    body = (
        '<usageflags><user name="TownVillage"><usage name="Town" />'
        '<usage name="Village" /></user></usageflags>'
    )
    path = write_xml(tmp_path / "anything.xml", f"<user_lists>{body}</user_lists>")
    assert xc.classify_mod_xml(path) == xc.KIND_LIMITSDEFINITIONUSER
    assert xc.classify_xml_text(f"<user_lists>{body}</user_lists>") == xc.KIND_LIMITSDEFINITIONUSER


def test_a_fragment_under_a_declaration_is_still_classified():
    text = (
        '<?xml version="1.0"?>\n'
        '<type name="A"><nominal>1</nominal></type>\n'
        '<type name="B"><nominal>2</nominal></type>'
    )
    assert xc.classify_xml_text(text) == xc.KIND_TYPES
