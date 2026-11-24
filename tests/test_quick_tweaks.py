"""Tests for the stock-file quick tweaks in app/presets/quick_tweaks.py (plans/35).

The fixture XML is Bohemia's own, read from DayZ-Central-Economy at the pinned
1.29 commit (tests/conftest.py::bohemia_ce): the <type>s from
dayzOffline.chernarusplus/db/types.xml, the <event>s from
dayzOffline.sakhal/db/events.xml, each as the file has it. Never replace them with
invented entries — the matchers are only proven against real ones.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.presets.quick_tweaks import (
    QUICK_TWEAKS,
    apply_tweak,
    reset_tweak,
    resolve_variant,
    variant_spec,
)
from app.xml.events_xml import parse_events_file
from app.xml.types_xml import parse_types_file, save_types_files, set_numeric_field
from tests.conftest import CE_1_29, CE_1_30, bohemia_ce, ce_block, ce_lines

#: The <type>s the tests use, from Chernarus' db/types.xml.
TYPE_NAMES = (
    "SparkPlug", "CarBattery", "CivSedanWheel", "CivSedanHood", "BakedBeansCan",
    "WaterBottle", "Hoodie_Black", "AliceBag_Green", "BandageDressing", "FirstAidKit",
    "AKM", "M67Grenade", "Hammer",
)
#: The <event>s the tests use, from Sakhal's db/events.xml.
EVENT_NAMES = (
    "VehicleCivilianSedan", "VehicleBoat", "VehicleMilitaryBoat", "StaticBoatFishing",
    "InfectedArmy",
)


def stock_elements(path: str, tag: str, names: tuple[str, ...], commit: str = CE_1_29) -> str:
    """The *names* elements of Bohemia's *path* (DayZ 1.29 unless *commit*
    says otherwise), each as it stands in the file, between the file's own
    first two lines and last."""
    lines = ce_lines(bohemia_ce(commit, path))
    elements = [line for name in names for line in ce_block(lines, f'<{tag} name="{name}">')]
    return "".join(lines[:2] + elements + lines[-1:])


def types_xml() -> str:
    return stock_elements("dayzOffline.chernarusplus/db/types.xml", "type", TYPE_NAMES)


def events_xml() -> str:
    return stock_elements("dayzOffline.sakhal/db/events.xml", "event", EVENT_NAMES)


SIDECAR = ".zsm-tweaks.json"


@pytest.fixture()
def mission(tmp_mission_dir: Path) -> Path:
    (tmp_mission_dir / "db" / "types.xml").write_text(types_xml(), encoding="utf-8")
    (tmp_mission_dir / "db" / "events.xml").write_text(events_xml(), encoding="utf-8")
    return tmp_mission_dir


def _types(mission: Path) -> dict[str, tuple]:
    """name -> (nominal, min, lifetime, restock)."""
    return {
        e.name: (e.nominal, e.min, e.lifetime, e.restock)
        for e in parse_types_file(mission / "db" / "types.xml").entries
    }


def _events(mission: Path) -> dict[str, tuple]:
    """name -> (nominal, min, max, lifetime, restock, active, children)."""
    return {
        e.name: (
            e.nominal, e.min, e.max, e.lifetime, e.restock, e.active,
            tuple((c.type, c.min, c.max, c.lootmin, c.lootmax) for c in e.children),
        )
        for e in parse_events_file(mission / "db" / "events.xml").entries
    }


def _sidecar(mission: Path) -> dict:
    return json.loads((mission / SIDECAR).read_text(encoding="utf-8"))["fields"]


def _doubled_type(vals: tuple) -> tuple:
    nominal, min_, *rest = vals
    return (nominal * 2, min_ * 2, *rest)


def _doubled_event(vals: tuple) -> tuple:
    nominal, min_, max_, lifetime, restock, active, children = vals
    return (
        nominal * 2, min_ * 2, max_ * 2, lifetime, restock, active,
        tuple((t, cmin * 2, cmax * 2, lmin, lmax) for t, cmin, cmax, lmin, lmax in children),
    )


PARTS = {"SparkPlug", "CarBattery", "CivSedanWheel", "CivSedanHood"}
CLOTHING, CONTAINERS, MEDICAL, TOOLS = {"Hoodie_Black"}, {"AliceBag_Green"}, {"BandageDressing", "FirstAidKit"}, {"Hammer"}

# (tweak, variant, types entries in scope, events in scope)
SCOPES = [
    ("vehicle_count", "cars", set(), {"VehicleCivilianSedan"}),
    ("vehicle_count", "cars_and_parts", PARTS, {"VehicleCivilianSedan"}),
    ("vehicle_count", "parts", PARTS, set()),
    ("boat_count", None, set(), {"VehicleBoat", "VehicleMilitaryBoat"}),
    ("food_amount", None, {"BakedBeansCan", "WaterBottle"}, set()),
    ("gear_amount", "clothing", CLOTHING, set()),
    ("gear_amount", "containers", CONTAINERS, set()),
    ("gear_amount", "medical", MEDICAL, set()),
    ("gear_amount", "tools", TOOLS, set()),
    ("gear_amount", "weapons", {"AKM"}, set()),
    ("gear_amount", "explosives", {"M67Grenade"}, set()),
    ("gear_amount", "all_gear", CLOTHING | CONTAINERS | MEDICAL | TOOLS, set()),
]


@pytest.mark.parametrize(("tweak", "variant", "items", "events"), SCOPES, ids=[f"{t}-{v}" for t, v, *_ in SCOPES])
def test_each_stack_scales_exactly_its_entries(mission, tweak, variant, items, events):
    types_path, events_path = mission / "db" / "types.xml", mission / "db" / "events.xml"
    types_before, events_before = _types(mission), _events(mission)
    types_bytes, events_bytes = types_path.read_bytes(), events_path.read_bytes()

    modified = apply_tweak(tweak, None, mission, 2.0, variant)

    # Only nominal/min/max and child min/max move: StaticBoat* and the infected event never.
    assert _types(mission) == {n: _doubled_type(v) if n in items else v for n, v in types_before.items()}
    assert _events(mission) == {n: _doubled_event(v) if n in events else v for n, v in events_before.items()}
    assert sorted(modified) == sorted(str(p) for p, hit in ((types_path, items), (events_path, events)) if hit)
    # A file the stack doesn't reach is not rewritten at all.
    if not items:
        assert types_path.read_bytes() == types_bytes
    if not events:
        assert events_path.read_bytes() == events_bytes
    # CivSedanHood's 0 stays 0, so the sidecar is what shows it was in scope.
    fields = _sidecar(mission)
    assert set(fields.get("db/types.xml", {})) == items
    assert set(fields.get("db/events.xml", {})) == events


def test_each_item_belongs_to_one_card(mission):
    """FirstAidKit is Medical, not Containers; SparkPlug is a car part, not a Tool."""
    for tweak, variant, name, expected in (
        ("gear_amount", "containers", "FirstAidKit", (15, 10)),
        ("gear_amount", "medical", "FirstAidKit", (30, 20)),
        ("gear_amount", "tools", "SparkPlug", (60, 40)),
        ("vehicle_count", "parts", "SparkPlug", (120, 80)),
    ):
        apply_tweak(tweak, None, mission, 2.0, variant)
        assert _types(mission)[name][:2] == expected, (tweak, variant)


def test_the_same_tier_twice_does_not_compound(mission):
    apply_tweak("vehicle_count", None, mission, 2.0, "cars_and_parts")
    once = [(mission / f).read_bytes() for f in ("db/types.xml", "db/events.xml", SIDECAR)]

    apply_tweak("vehicle_count", None, mission, 2.0, "cars_and_parts")

    assert [(mission / f).read_bytes() for f in ("db/types.xml", "db/events.xml", SIDECAR)] == once
    assert _events(mission)["VehicleCivilianSedan"][:3] == (8, 4, 10)


@pytest.mark.parametrize("first", [2.0, 3.0, 0.5, 0.25, 0.0])
def test_normal_restores_the_values_before_the_first_tweak(mission, first):
    types_before, events_before = _types(mission), _events(mission)

    apply_tweak("vehicle_count", None, mission, first, "cars_and_parts")
    apply_tweak("boat_count", None, mission, first)
    apply_tweak("vehicle_count", None, mission, 1.0, "cars_and_parts")
    apply_tweak("boat_count", None, mission, 1.0)

    assert _types(mission) == types_before
    assert _events(mission) == events_before


def test_scaling_keeps_min_nominal_max_ordered(mission):
    for tier in QUICK_TWEAKS["boat_count"].tiers + QUICK_TWEAKS["food_amount"].tiers:
        apply_tweak("boat_count", None, mission, tier.multiplier)
        apply_tweak("vehicle_count", None, mission, tier.multiplier, "cars")
        for name, (nominal, min_, max_, *_rest, children) in _events(mission).items():
            if name.startswith("Vehicle"):
                assert min_ <= nominal <= max_, (tier.name, name)
                assert all(cmin <= cmax for _t, cmin, cmax, *_ in children), (tier.name, name)


def test_a_hand_edit_after_a_tweak_becomes_the_new_base(mission):
    apply_tweak("food_amount", None, mission, 2.0)
    assert _types(mission)["BakedBeansCan"][:2] == (30, 24)

    tf = parse_types_file(mission / "db" / "types.xml")
    set_numeric_field(next(e for e in tf.entries if e.name == "BakedBeansCan"), "nominal", 20)
    save_types_files([tf])

    apply_tweak("food_amount", None, mission, 1.0)
    assert _types(mission)["BakedBeansCan"][:2] == (20, 12)
    apply_tweak("food_amount", None, mission, 2.0)
    assert _types(mission)["BakedBeansCan"][:2] == (40, 24)


def test_a_map_editor_backup_between_applies_changes_nothing(mission):
    """A new events.xml.backup between applies is not a baseline."""
    events_path = mission / "db" / "events.xml"
    apply_tweak("boat_count", None, mission, 2.0)
    shutil.copy2(events_path, events_path.with_name("events.xml.backup"))

    apply_tweak("boat_count", None, mission, 2.0)
    assert _events(mission)["VehicleBoat"][:3] == (28, 22, 28)

    apply_tweak("boat_count", None, mission, 1.0)
    assert _events(mission)["VehicleBoat"][:3] == (14, 11, 14)


def test_custom_types_are_never_touched(mission):
    custom = mission / "custom" / "some-mod-types.xml"
    custom.parent.mkdir()
    custom.write_text(types_xml(), encoding="utf-8")
    before = custom.read_bytes()

    modified = apply_tweak("food_amount", None, mission, 3.0)

    assert modified == [str(mission / "db" / "types.xml")]
    assert custom.read_bytes() == before


def test_admin_comments_in_types_xml_survive(mission):
    path = mission / "db" / "types.xml"
    note = "<!-- raised for the weekend event -->"
    path.write_text(types_xml().replace("<types>\n", f"<types>\n    {note}\n", 1), encoding="utf-8")

    apply_tweak("food_amount", None, mission, 2.0)

    assert note in path.read_text(encoding="utf-8")
    assert _types(mission)["BakedBeansCan"][:2] == (30, 24)


@pytest.mark.parametrize("junk", [
    "{not json",
    "[]",
    '{"version": 2, "fields": {}}',
    '{"version": 1, "fields": {"db/types.xml": []}}',
    '{"version": 1, "fields": {"db/types.xml": {"BakedBeansCan": 5}}}',
    '{"version": 1, "fields": {"db/types.xml": {"BakedBeansCan": {"nominal": {"base": "7", "written": 15}}}}}',
])
def test_a_corrupt_sidecar_rebaselines_from_disk(mission, junk):
    (mission / SIDECAR).write_text(junk, encoding="utf-8")

    apply_tweak("food_amount", None, mission, 2.0)

    assert _types(mission)["BakedBeansCan"][:2] == (30, 24)
    assert _sidecar(mission)["db/types.xml"]["BakedBeansCan"]["nominal"] == {"base": 15, "written": 30}


def test_overlapping_stacks_share_one_baseline(mission):
    apply_tweak("gear_amount", None, mission, 2.0, "medical")
    apply_tweak("gear_amount", None, mission, 1.0, "all_gear")

    assert _types(mission)["BandageDressing"][:2] == (40, 30)


def test_the_sidecar_is_written_only_after_the_xml_save(mission, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.xml.types_xml.save_xml", fail)
    with pytest.raises(OSError):
        apply_tweak("food_amount", None, mission, 2.0)

    assert not (mission / SIDECAR).exists()


def test_a_mission_without_the_stock_files_is_a_no_op(tmp_mission_dir):
    assert apply_tweak("vehicle_count", None, tmp_mission_dir, 2.0, "cars_and_parts") == []
    assert not (tmp_mission_dir / SIDECAR).exists()


def test_new_tweaks_follow_the_existing_four():
    assert list(QUICK_TWEAKS) == [
        "zombie_count", "predator_count", "wild_animal_count", "farm_animal_count",
        "vehicle_count", "boat_count", "food_amount", "gear_amount",
    ]


def test_tier_ladders():
    ladder = lambda key: [(t.name, t.multiplier) for t in QUICK_TWEAKS[key].tiers]  # noqa: E731
    items = [("None", 0.0), ("Scarce", 0.25), ("Low", 0.5), ("Normal", 1.0), ("High", 2.0), ("Abundant", 3.0)]
    vehicles = [("None", 0.0), ("Few", 0.5), ("Normal", 1.0), ("More", 2.0), ("Lots", 3.0)]
    assert ladder("food_amount") == ladder("gear_amount") == items
    assert ladder("vehicle_count") == ladder("boat_count") == vehicles


def test_resolve_variant():
    assert resolve_variant("gear_amount", None) == "clothing"
    assert resolve_variant("vehicle_count", "parts") == "parts"
    assert resolve_variant("boat_count", None) is None
    with pytest.raises(KeyError):
        resolve_variant("gear_amount", "food")
    with pytest.raises(KeyError):
        resolve_variant("zombie_count", "cars")


def test_the_zero_warning_belongs_to_stacks_that_spawn_events():
    assert variant_spec("vehicle_count", "cars").has_events
    assert variant_spec("vehicle_count", "cars_and_parts").has_events
    assert variant_spec("vehicle_count", "motorbikes").has_events
    assert not variant_spec("vehicle_count", "parts").has_events
    assert variant_spec("boat_count").has_events
    assert not variant_spec("food_amount").has_events
    assert variant_spec("zombie_count") is None
    for key in ("vehicle_count", "boat_count"):
        assert QUICK_TWEAKS[key].zero_warning in QUICK_TWEAKS[key].description
    assert QUICK_TWEAKS["food_amount"].zero_warning == QUICK_TWEAKS["gear_amount"].zero_warning == ""


#: DayZ 1.30's Chernarus events: the first release with motorbikes on every stock map (plans/95).
EVENTS_1_30 = ("VehicleCivilianSedan", "VehicleMotorbike", "VehicleBoat")


@pytest.fixture()
def mission_1_30(tmp_mission_dir: Path) -> Path:
    (tmp_mission_dir / "db" / "events.xml").write_text(
        stock_elements("dayzOffline.chernarusplus/db/events.xml", "event", EVENTS_1_30, CE_1_30),
        encoding="utf-8",
    )
    return tmp_mission_dir


@pytest.mark.parametrize(("variant", "scaled"), [
    ("motorbikes", {"VehicleMotorbike"}),
    ("cars", {"VehicleCivilianSedan"}),
    ("cars_and_parts", {"VehicleCivilianSedan"}),
])
def test_cars_and_motorbikes_scale_apart(mission_1_30, variant, scaled):
    before = _events(mission_1_30)
    apply_tweak("vehicle_count", None, mission_1_30, 2.0, variant)
    assert _events(mission_1_30) == {n: _doubled_event(v) if n in scaled else v for n, v in before.items()}


def test_a_car_apply_never_undoes_a_motorbike_apply(mission_1_30):
    before = _events(mission_1_30)
    apply_tweak("vehicle_count", None, mission_1_30, 2.0, "motorbikes")
    apply_tweak("vehicle_count", None, mission_1_30, 0.0, "cars")
    reset_tweak("vehicle_count", None, mission_1_30, "cars")
    assert _events(mission_1_30) == {
        n: _doubled_event(v) if n == "VehicleMotorbike" else v for n, v in before.items()
    }


def test_only_the_motorbike_variant_links_to_the_map_editor():
    links = {v.key: v.map_event for v in QUICK_TWEAKS["vehicle_count"].variants}
    assert links == {"cars": "", "cars_and_parts": "", "parts": "", "motorbikes": "VehicleMotorbike"}


# ---------------------------------------------------------------------------
# Reset to default
# ---------------------------------------------------------------------------


def test_reset_puts_the_stack_back_and_drops_the_sidecar(mission):
    types_before, events_before = _types(mission), _events(mission)
    apply_tweak("vehicle_count", None, mission, 3.0, "cars_and_parts")

    modified = reset_tweak("vehicle_count", None, mission, "cars_and_parts")

    assert sorted(modified) == sorted(str(mission / "db" / f) for f in ("types.xml", "events.xml"))
    assert _types(mission) == types_before
    assert _events(mission) == events_before
    assert not (mission / SIDECAR).exists()


def test_reset_touches_only_the_selected_stack(mission):
    apply_tweak("gear_amount", None, mission, 2.0, "clothing")
    apply_tweak("gear_amount", None, mission, 2.0, "medical")

    reset_tweak("gear_amount", None, mission, "medical")

    assert _types(mission)["BandageDressing"][:2] == (40, 30)
    assert _types(mission)["FirstAidKit"][:2] == (15, 10)
    assert _types(mission)["Hoodie_Black"][:2] == (90, 70)  # clothing is still tweaked
    assert set(_sidecar(mission)["db/types.xml"]) == {"Hoodie_Black"}


def test_reset_with_nothing_tweaked_is_a_no_op(mission):
    before = (mission / "db" / "types.xml").read_bytes()

    assert reset_tweak("food_amount", None, mission) == []

    assert (mission / "db" / "types.xml").read_bytes() == before
    assert not (mission / SIDECAR).exists()


def test_reset_keeps_a_hand_edit_and_forgets_the_record(mission):
    apply_tweak("food_amount", None, mission, 2.0)
    tf = parse_types_file(mission / "db" / "types.xml")
    set_numeric_field(next(e for e in tf.entries if e.name == "BakedBeansCan"), "nominal", 99)
    save_types_files([tf])

    reset_tweak("food_amount", None, mission)

    # The hand edit is the default now; min still held the tweak, so it goes back.
    assert _types(mission)["BakedBeansCan"][:2] == (99, 12)
    assert _types(mission)["WaterBottle"][:2] == (100, 85)
    assert not (mission / SIDECAR).exists()


def test_after_a_reset_the_next_tweak_baselines_from_disk(mission):
    apply_tweak("boat_count", None, mission, 3.0)
    reset_tweak("boat_count", None, mission)

    apply_tweak("boat_count", None, mission, 2.0)

    assert _events(mission)["VehicleBoat"][:3] == (28, 22, 28)


# Territory/globals cards keep their baselines in the sidecar too. The territory
# sample is the one tests/test_zombie_territories_xml.py uses.
TERRITORIES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<zombie_territories>
    <territory color="#ff0000">
        <zone name="Elektro" smin="3" smax="8" dmin="1" dmax="3" x="10500.0" z="2200.0" r="200.0"/>
        <zone name="Cherno" smin="5" smax="12" dmin="2" dmax="5" x="6700.0" z="2500.0" r="300.0"/>
    </territory>
</zombie_territories>
"""

ANIMAL_GLOBALS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<globals>
    <var name="AnimalMaxCount" type="0" value="200"/>
    <var name="ZombieMaxCount" type="0" value="800"/>
</globals>
"""


@pytest.fixture()
def animal_mission(tmp_mission_dir: Path) -> Path:
    (tmp_mission_dir / "env").mkdir()
    for name in ("zombie_territories.xml", "wolf_territories.xml"):
        (tmp_mission_dir / "env" / name).write_text(TERRITORIES_XML, encoding="utf-8")
    (tmp_mission_dir / "db" / "globals.xml").write_text(ANIMAL_GLOBALS_XML, encoding="utf-8")
    return tmp_mission_dir


def _globals(mission: Path) -> dict[str, str]:
    from app.xml.globals_xml import parse_globals_file

    return {e.name: e.value for e in parse_globals_file(mission / "db" / "globals.xml").entries}


def _zones(path: Path) -> list[tuple]:
    from app.xml.zombie_territories_xml import parse_territories

    return [
        (z.smin, z.smax, z.dmin, z.dmax)
        for t in parse_territories(path).territories for z in t.zones
    ]


def _animal_mission_tracking(mission: Path) -> Path:
    return mission / "env" / ".animal_tweaks.json"


def test_reset_zombie_count_restores_the_pre_tweak_values(animal_mission):
    zt = animal_mission / "env" / "zombie_territories.xml"
    zones_before = _zones(zt)
    apply_tweak("zombie_count", None, animal_mission, 3.0)
    assert _globals(animal_mission)["ZombieMaxCount"] == "2400"

    reset_tweak("zombie_count", None, animal_mission)

    assert _globals(animal_mission)["ZombieMaxCount"] == "800"
    assert _zones(zt) == zones_before


def test_a_missing_zombie_cap_scales_the_engine_default(animal_mission):
    """Without the var the server runs on 1000, Bohemia's documented default
    (central-economy-configuration.wiki: ZombieMaxCount, Integer, 1000)."""
    globals_xml = animal_mission / "db" / "globals.xml"
    globals_xml.write_text(
        ANIMAL_GLOBALS_XML.replace('    <var name="ZombieMaxCount" type="0" value="800"/>\n', ""),
        encoding="utf-8",
    )

    apply_tweak("zombie_count", None, animal_mission, 2.0)

    assert _globals(animal_mission)["ZombieMaxCount"] == "2000"


def test_a_cap_that_is_not_a_whole_number_is_left_alone(animal_mission):
    """int() raised on it and stopped the tweak, for the animal ones after the
    territory files were written."""
    globals_xml = animal_mission / "db" / "globals.xml"
    globals_xml.write_text(
        ANIMAL_GLOBALS_XML.replace('value="200"', 'value="lots"').replace('value="800"', 'value="800.5"'),
        encoding="utf-8",
    )
    zt = animal_mission / "env" / "zombie_territories.xml"
    wolves = animal_mission / "env" / "wolf_territories.xml"
    zombies_before, wolves_before = _zones(zt), _zones(wolves)

    apply_tweak("zombie_count", None, animal_mission, 3.0)
    apply_tweak("predator_count", None, animal_mission, 3.0)

    assert _globals(animal_mission) == {"AnimalMaxCount": "lots", "ZombieMaxCount": "800.5"}
    assert _zones(zt) != zombies_before
    assert _zones(wolves) != wolves_before


def test_reset_an_animal_card_frees_the_shared_cap(animal_mission):
    wolves = animal_mission / "env" / "wolf_territories.xml"
    tracking = animal_mission / "env" / ".animal_tweaks.json"
    zones_before = _zones(wolves)
    apply_tweak("predator_count", None, animal_mission, 3.0)
    apply_tweak("farm_animal_count", None, animal_mission, 2.0)
    assert _globals(animal_mission)["AnimalMaxCount"] == "600"

    # Predators reset: the cap falls back to the highest remaining category.
    reset_tweak("predator_count", None, animal_mission)
    assert _globals(animal_mission)["AnimalMaxCount"] == "400"
    assert _zones(wolves) == zones_before
    assert json.loads(tracking.read_text(encoding="utf-8")) == {"farm_animal_count": 2.0}

    # Resetting the last category restores the backup value, it does not zero it.
    reset_tweak("farm_animal_count", None, animal_mission)
    assert _globals(animal_mission)["AnimalMaxCount"] == "200"
    assert not tracking.exists()


@pytest.mark.parametrize(
    "tweak", ["zombie_count", "predator_count", "wild_animal_count", "farm_animal_count"],
)
def test_reset_before_any_tweak_touches_nothing(animal_mission, tweak):
    """The first cut re-applied 1x here, which rewrote files nobody had tweaked."""
    before = {p: p.read_bytes() for p in sorted(animal_mission.rglob("*.xml"))}

    assert reset_tweak(tweak, None, animal_mission) == []

    assert {p: p.read_bytes() for p in sorted(animal_mission.rglob("*.xml"))} == before
    assert list(animal_mission.rglob("*.backup")) == []
    assert not _animal_mission_tracking(animal_mission).exists()


def test_reset_restores_the_backup_bytes(animal_mission):
    """A byte copy, so indentation and comments come back exactly as they were."""
    zt = animal_mission / "env" / "zombie_territories.xml"
    original = zt.read_bytes()
    apply_tweak("zombie_count", None, animal_mission, 3.0)
    assert zt.read_bytes() != original

    reset_tweak("zombie_count", None, animal_mission)

    assert zt.read_bytes() == original


def test_a_second_reset_rewrites_nothing(animal_mission):
    zt = animal_mission / "env" / "zombie_territories.xml"
    apply_tweak("zombie_count", None, animal_mission, 2.0)
    reset_tweak("zombie_count", None, animal_mission)
    after_first = (zt.read_bytes(), (animal_mission / "db" / "globals.xml").read_bytes())

    assert reset_tweak("zombie_count", None, animal_mission) == []

    assert (zt.read_bytes(), (animal_mission / "db" / "globals.xml").read_bytes()) == after_first


def test_a_zone_edited_after_a_zombie_tweak_becomes_the_new_base(animal_mission):
    """Every apply restored zombie_territories.xml from its first .backup, so a
    zone edited by hand after the first tweak went back at the next one."""
    zt = animal_mission / "env" / "zombie_territories.xml"
    apply_tweak("zombie_count", None, animal_mission, 2.0)
    assert _zones(zt)[0] == (6, 16, 2, 6)
    zt.write_text(zt.read_text(encoding="utf-8").replace('smax="16"', 'smax="30"'), encoding="utf-8")

    apply_tweak("zombie_count", None, animal_mission, 1.5)

    elektro = _zones(zt)[0]
    assert elektro[1] == 45  # the hand edit, 30, is the new base
    assert elektro[0] == 5   # an untouched field still scales from its base, 3
    reset_tweak("zombie_count", None, animal_mission)
    assert _zones(zt)[0] == (3, 30, 1, 3)


def test_a_cap_edited_after_a_tweak_becomes_the_new_base(animal_mission):
    from app.presets.quick_tweaks import current_multiplier

    apply_tweak("zombie_count", None, animal_mission, 3.0)
    assert current_multiplier("zombie_count", animal_mission) == 3.0
    globals_xml = animal_mission / "db" / "globals.xml"
    globals_xml.write_text(globals_xml.read_text(encoding="utf-8").replace('value="2400"', 'value="500"'), encoding="utf-8")
    assert current_multiplier("zombie_count", animal_mission) is None  # Custom

    apply_tweak("zombie_count", None, animal_mission, 0.5)

    assert _globals(animal_mission)["ZombieMaxCount"] == "250"
    assert current_multiplier("zombie_count", animal_mission) == 0.5


def test_every_tweak_can_be_reset():
    from app.presets.quick_tweaks import _RESET_FUNCTIONS

    assert set(_RESET_FUNCTIONS) == set(QUICK_TWEAKS)
    with pytest.raises(KeyError):
        reset_tweak("does-not-exist", None, Path("."))
    with pytest.raises(KeyError):
        reset_tweak("gear_amount", None, Path("."), "food")
