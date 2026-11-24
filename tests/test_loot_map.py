"""The loot layer's index and matcher (plans/82): app/maps/loot_map.py.

Every fixture is invented, in Bohemia's shape and a few lines long. Never paste
a real mission or mod file into a test (plans/65).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.maps import building_map, loot_map
from app.maps.loot_map import index_for, match, owners

PROTO = """<?xml version="1.0" encoding="UTF-8"?>
<prototype>
  <group name="Land_Shed" lootmax="4">
    <usage name="Farm"/>
    <container name="lootFloor">
      <category name="tools"/>
      <point pos="1 0 1" range="0.5" height="1"/>
    </container>
    <container name="lootshelves">
      <category name="clothes"/>
      <tag name="shelves"/>
      <point pos="2 1 1" range="0.5" height="1"/>
    </container>
  </group>
  <group name="Land_Barracks" lootmax="8">
    <usage name="Military"/>
    <container name="lootFloor">
      <category name="weapons"/>
      <point pos="1 0 1" range="0.5" height="1"/>
    </container>
    <container name="lootEmpty">
      <category name="food"/>
    </container>
  </group>
</prototype>
"""

POS = """<?xml version="1.0" encoding="UTF-8"?>
<map>
  <group name="Land_Shed" pos="100 1 200" rpy="0 0 0" a="0"/>
  <group name="Land_Shed" pos="300 1 400" rpy="0 0 0" a="0"/>
  <group name="Land_Barracks" pos="5000 1 6000" rpy="0 0 0" a="0"/>
</map>
"""

TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<types>
  <type name="Hammer">
    <nominal>10</nominal>
    <category name="tools"/>
    <usage name="Farm"/>
  </type>
  <type name="Rifle">
    <nominal>5</nominal>
    <category name="weapons"/>
    <usage name="Military"/>
    <value name="Tier3"/>
  </type>
  <type name="Jacket">
    <nominal>5</nominal>
    <category name="clothes"/>
    <tag name="shelves"/>
    <usage name="Farm"/>
  </type>
  <type name="Boot">
    <nominal>5</nominal>
    <category name="clothes"/>
    <tag name="floor"/>
    <usage name="Farm"/>
  </type>
  <type name="Moon_Rock">
    <nominal>5</nominal>
    <category name="tools"/>
    <usage name="Moon"/>
  </type>
  <type name="Can">
    <nominal>5</nominal>
    <category name="food"/>
    <usage name="Military"/>
  </type>
  <type name="Anything">
    <nominal>5</nominal>
    <category name="tools"/>
  </type>
  <type name="Zero">
    <nominal>0</nominal>
    <category name="tools"/>
    <usage name="Farm"/>
  </type>
  <type name="Made">
    <nominal>5</nominal>
    <flags crafted="1" deloot="0"/>
    <category name="tools"/>
    <usage name="Farm"/>
  </type>
  <type name="Heli_Gun">
    <nominal>5</nominal>
    <flags crafted="0" deloot="1"/>
    <category name="weapons"/>
    <usage name="Military"/>
  </type>
</types>
"""

ECONOMYCORE = """<?xml version="1.0" encoding="UTF-8"?>
<economycore>
  <ce folder="custom">
    <file name="fox-weapons-2.0-types_guns.xml" type="types"/>
    <file name="user-mine.xml" type="types"/>
  </ce>
</economycore>
"""


@pytest.fixture(autouse=True)
def _clear_caches():
    loot_map._cache.clear()
    building_map._cache.clear()
    yield
    loot_map._cache.clear()
    building_map._cache.clear()


@pytest.fixture()
def mission(tmp_path: Path) -> Path:
    server = tmp_path / "server"
    m = server / "mpmissions" / "dayzOffline.chernarusplus"
    (m / "db").mkdir(parents=True)
    (m / "custom").mkdir()
    (server / "@FOX WEAPONS 2.0").mkdir()
    (m / "mapgroupproto.xml").write_text(PROTO)
    (m / "mapgrouppos.xml").write_text(POS)
    (m / "db" / "types.xml").write_text(TYPES)
    (m / "cfgeconomycore.xml").write_text(ECONOMYCORE)
    (m / "custom" / "fox-weapons-2.0-types_guns.xml").write_text(
        "<types>"
        '<type name="Fox_Rifle"><nominal>3</nominal><category name="weapons"/>'
        '<usage name="Military"/></type>'
        '<type name="Fox_Pistol"><nominal>3</nominal><category name="weapons"/>'
        '<usage name="Military"/></type>'
        '<type name="Hammer"><nominal>2</nominal><category name="weapons"/>'
        '<usage name="Military"/></type>'
        "</types>"
    )
    (m / "custom" / "user-mine.xml").write_text(
        '<types><type name="My_Hat"><nominal>1</nominal><category name="clothes"/>'
        '<tag name="shelves"/><usage name="Farm"/></type></types>'
    )
    return m


def _one(mission: Path, name: str) -> dict:
    index = index_for(mission)
    result = match(index, [name.lower()])
    return {**result["items"][0], "points": result["points"], "groups": result["groups"],
            "tiers_unchecked": result["tiers_unchecked"]}


# ── Matching ─────────────────────────────────────────────────────────────


def test_a_shared_usage_and_a_container_with_the_category_is_a_match(mission):
    row = _one(mission, "Fox_Rifle")
    assert row["buildings"] == 1
    assert row["kinds"] == 1
    assert row["points"] == [5000, 6000]
    assert row["groups"] == [
        {"name": "Land_Barracks", "usages": ["Military"], "buildings": 1, "first": [5000, 6000], "containers": ["lootFloor"]}
    ]


def test_an_item_whose_usage_no_building_has_matches_nothing(mission):
    row = _one(mission, "Moon_Rock")
    assert row["buildings"] == 0
    assert row["reason"] == "no building has its usage"
    assert row["points"] == []


def test_the_category_must_be_in_one_container_of_the_group(mission):
    # A shed has usage Farm, but none of its containers takes weapons.
    (mission / "custom" / "user-mine.xml").write_text(
        '<types><type name="Shed_Gun"><nominal>3</nominal><category name="weapons"/>'
        '<usage name="Farm"/></type></types>'
    )
    row = _one(mission, "Shed_Gun")
    assert row["buildings"] == 0
    assert row["reason"] == "no container takes its category/tag"


def test_a_tagged_item_needs_a_container_with_that_tag(mission):
    assert _one(mission, "Jacket")["buildings"] == 2
    boot = _one(mission, "Boot")
    assert boot["buildings"] == 0
    assert boot["reason"] == "no container takes its category/tag"


def test_an_item_without_usage_is_not_limited_by_usage(mission):
    row = _one(mission, "Anything")
    assert row["buildings"] == 2
    assert row["points"] == [100, 200, 300, 400]


def test_a_container_without_points_is_no_place_to_spawn(mission):
    row = _one(mission, "Can")
    assert row["buildings"] == 0
    assert row["reason"] == "no container takes its category/tag"


def test_user_flag_groups_are_expanded(mission):
    (mission / "cfglimitsdefinitionuser.xml").write_text(
        "<user_lists>"
        '<usageflags><user name="Rural"><usage name="Farm"/><usage name="Village"/></user></usageflags>'
        '<valueflags><user name="TierLow"><value name="Tier1"/><value name="Tier2"/></user></valueflags>'
        "</user_lists>"
    )
    (mission / "custom" / "user-mine.xml").write_text(
        '<types><type name="My_Hat"><nominal>1</nominal><category name="clothes"/>'
        '<tag name="shelves"/><usage user="Rural"/><value user="TierLow"/></type></types>'
    )
    row = _one(mission, "My_Hat")
    assert row["usages"] == ["Farm", "Village"]
    assert row["values"] == ["Tier1", "Tier2"]
    assert row["buildings"] == 2


def test_value_tiers_are_reported_not_matched(mission):
    row = _one(mission, "Rifle")
    assert row["values"] == ["Tier3"]
    assert row["buildings"] == 1
    assert row["tiers_unchecked"] is True
    assert _one(mission, "Fox_Rifle")["tiers_unchecked"] is False


def test_a_group_value_is_matched_when_the_file_has_one(mission):
    (mission / "mapgroupproto.xml").write_text(PROTO.replace(
        '<usage name="Military"/>', '<usage name="Military"/><value name="Tier1"/>'
    ))
    row = _one(mission, "Rifle")  # Tier3 only
    assert row["buildings"] == 0
    assert row["reason"] == "no container takes its category/tag"
    (mission / "mapgroupproto.xml").write_text(PROTO.replace(
        '<usage name="Military"/>', '<usage name="Military"/><value name="Tier3"/>'
    ))
    loot_map._cache.clear()
    row = _one(mission, "Rifle")
    assert row["buildings"] == 1
    assert row["tiers_unchecked"] is False


def test_nominal_zero_crafted_and_deloot_say_why(mission):
    index = index_for(mission)
    result = match(index, ["zero", "made", "heli_gun"])
    assert [r["reason"] for r in result["items"]] == ["nominal 0", "crafted", "event loot only"]
    assert result["points"] == []
    assert result["matched"] == 0


# ── Where each type comes from ───────────────────────────────────────────


def test_the_last_registered_definition_wins_and_every_owner_is_kept(mission):
    index = index_for(mission)
    hammer = index.types["hammer"]
    assert hammer.owner == "@FOX WEAPONS 2.0"
    assert hammer.category == "weapons"
    assert hammer.defined_by == ("", "@FOX WEAPONS 2.0")


def test_a_custom_file_belongs_to_its_mod(mission):
    index = index_for(mission)
    assert index.types["fox_rifle"].owner == "@FOX WEAPONS 2.0"
    rows = {r["key"]: r for r in owners(index)}
    assert rows["@FOX WEAPONS 2.0"] == {"key": "@FOX WEAPONS 2.0", "label": "@FOX WEAPONS 2.0", "count": 3}
    assert rows["vanilla"]["label"] == "Vanilla (db/types.xml)"
    assert owners(index)[0]["key"] == "vanilla"


def test_a_manual_file_is_named_as_one(mission):
    index = index_for(mission)
    assert index.types["my_hat"].owner == "@user:mine"
    assert match(index, ["my_hat"])["items"][0]["owner_label"] == "your manual XML file mine"


def test_an_unregistered_custom_file_is_not_loaded(mission):
    (mission / "custom" / "stray-types.xml").write_text(
        '<types><type name="Stray"><nominal>1</nominal></type></types>'
    )
    assert "stray" not in index_for(mission).types


def test_a_parked_registration_is_not_loaded(mission):
    (mission / "cfgeconomycore.xml").write_text(
        ECONOMYCORE.replace(
            '    <file name="fox-weapons-2.0-types_guns.xml" type="types"/>\n', ""
        ).replace(
            "</economycore>",
            '<!-- DISABLED @FOX WEAPONS 2.0: <ce folder="custom">'
            '<file name="fox-weapons-2.0-types_guns.xml" type="types"/></ce> -->\n</economycore>',
        )
    )
    index = index_for(mission)
    assert "fox_rifle" not in index.types
    assert index.types["hammer"].owner == ""
    assert "@FOX WEAPONS 2.0" not in {r["key"] for r in owners(index)}


def test_a_registration_outside_the_mission_is_not_read(mission):
    server = mission.parent.parent
    (server / "outside-types.xml").write_text(
        '<types><type name="Outside_Class"><nominal>1</nominal></type></types>'
    )
    (mission / "cfgeconomycore.xml").write_text(ECONOMYCORE.replace(
        "</economycore>",
        '  <ce folder="../.."><file name="outside-types.xml" type="types"/></ce>\n'
        '  <ce folder="custom"><file name="../../../outside-types.xml" type="types"/></ce>\n'
        "</economycore>",
    ))
    assert "outside_class" not in index_for(mission).types


# ── Reading third-party files ────────────────────────────────────────────


def test_group_names_match_without_regard_to_case(mission):
    (mission / "mapgrouppos.xml").write_text(POS.replace('name="Land_Shed"', 'name="LAND_SHED"'))
    assert _one(mission, "Jacket")["buildings"] == 2


def test_a_mapgroupproto_over_the_limit_is_unreadable_not_an_error(mission, monkeypatch):
    monkeypatch.setattr(loot_map, "MAX_PROTO_BYTES", 100)
    index = index_for(mission)
    assert index.problem == "unreadable: mapgroupproto.xml"
    assert any("over the" in u for u in index.unreadable)
    assert match(index, ["hammer"])["points"] == []


def test_an_unparseable_mapgroupproto_is_unreadable_not_an_error(mission):
    (mission / "mapgroupproto.xml").write_text("<prototype><group name=")
    index = index_for(mission)
    assert index.problem == "unreadable: mapgroupproto.xml"
    assert index.groups == {}


def test_entity_expansion_in_mapgroupproto_is_refused(mission):
    (mission / "mapgroupproto.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>\n'
        '<prototype><group name="&lol2;"><usage name="Farm"/></group></prototype>'
    )
    index = index_for(mission)
    assert index.problem == "unreadable: mapgroupproto.xml"
    assert any("unsafe" in u for u in index.unreadable)


def test_too_many_containers_is_unreadable(mission, monkeypatch):
    monkeypatch.setattr(loot_map, "MAX_CONTAINERS_PER_GROUP", 1)
    assert index_for(mission).problem == "unreadable: mapgroupproto.xml"


def test_a_missing_mapgroupproto_or_mapgrouppos_is_named(mission):
    (mission / "mapgrouppos.xml").unlink()
    assert index_for(mission).problem == "no_mapgrouppos"
    (mission / "mapgroupproto.xml").unlink()
    assert index_for(mission).problem == "no_mapgroupproto"


# ── Cache and memo ───────────────────────────────────────────────────────


def _touch(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_the_index_is_read_again_after_a_file_changes_and_not_before(mission, monkeypatch):
    first = index_for(mission)
    assert index_for(mission) is first

    builds = []
    real = loot_map.build_index
    monkeypatch.setattr(loot_map, "build_index", lambda m: builds.append(m) or real(m))
    assert index_for(mission) is first
    assert builds == []

    types_file = mission / "custom" / "fox-weapons-2.0-types_guns.xml"
    types_file.write_text(
        '<types><type name="Fox_Knife"><nominal>3</nominal><category name="weapons"/>'
        '<usage name="Military"/></type></types>'
    )
    _touch(types_file)
    again = index_for(mission)
    assert builds == [mission]
    assert "fox_knife" in again.types
    assert "fox_rifle" not in again.types


def test_a_mods_items_with_one_signature_are_matched_once(mission, monkeypatch):
    calls = []
    real = loot_map.groups_for
    monkeypatch.setattr(loot_map, "groups_for", lambda item, index: calls.append(item.name) or real(item, index))
    index = index_for(mission)
    result = match(index, ["fox_rifle", "fox_pistol", "hammer"])
    assert len(calls) == 1
    assert [r["buildings"] for r in result["items"]] == [1, 1, 1]
    assert result["points"] == [5000, 6000]



# ── One building ─────────────────────────────────────────────────────────


def test_items_by_group_is_the_reverse_of_the_match(mission):
    index = index_for(mission)
    by_group = loot_map.items_by_group(index)
    assert [index.types[k].name for k in by_group["land_shed"]] == ["Anything", "Jacket", "My_Hat"]
    assert [index.types[k].name for k in by_group["land_barracks"]] == [
        "Fox_Pistol", "Fox_Rifle", "Hammer", "Rifle"]
    assert loot_map.items_by_group(index) is by_group


def test_building_at_takes_the_nearest_within_the_radius(mission):
    index = index_for(mission)
    assert loot_map.building_at(index, 290, 390, 30) == ("land_shed", (300, 400))
    assert loot_map.building_at(index, 200, 300, 30) is None


def test_building_report_lists_containers_with_points_only(mission):
    index = index_for(mission)
    report = loot_map.building_report(index, "land_barracks", (5000, 6000))
    assert report["name"] == "Land_Barracks"
    assert [c["name"] for c in report["containers"]] == ["lootFloor"]
    assert report["on_map"] == 1
    assert {i["name"] for i in report["items"]} >= {"Rifle", "Fox_Rifle"}
