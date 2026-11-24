"""Tests for the "Only X" spawn-test mode (plans/37, Package B).

Package A writes ``init.c``; B writes the loot economy instead, so the two
share only the session machinery. What is worth pinning here is not that the
numbers change — it is **which** entries change and which are left alone, and
that End puts every one of them back including the tweak sidecars.

The stock halves of the fixture come from ``test_quick_tweaks``'s readers
rather than a second excerpt: they are Bohemia's own entries, read from
DayZ-Central-Economy at a pinned commit (Chernarus ``db/types.xml``, Sakhal
``db/events.xml``), and one reader means a correction to them can't leave the
other saying something the game does not.
The modded halves — a ``custom/`` types file, a ``custom/`` events file and the
``cfgeconomycore.xml`` that registers them — are written here, because that is
the shape a merge produces and no stock file has it.
"""

from __future__ import annotations

import json

import pytest

from app.ops import spawn_test_ops as st
from tests.conftest import set_instance_id, write_test_mods
from tests.test_quick_tweaks import events_xml, types_xml

MOD = "@Bikes"
CLASSES = ["Old_Bike", "Old_Bike_Red", "Bicycle_Base"]

# What a merge leaves in custom/ for a mod that ships both fragments.
MOD_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<types>
    <type name="Old_Bike">
        <nominal>12</nominal>
        <lifetime>3888000</lifetime>
        <restock>0</restock>
        <min>6</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0"/>
        <category name="vehiclesparts"/>
    </type>
    <type name="Old_Bike_Red">
        <nominal>8</nominal>
        <lifetime>3888000</lifetime>
        <restock>0</restock>
        <min>4</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0"/>
        <category name="vehiclesparts"/>
    </type>
</types>
"""

MOD_EVENTS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<events>
    <event name="VehicleBicycle">
        <nominal>6</nominal>
        <min>3</min>
        <max>9</max>
        <lifetime>300</lifetime>
        <restock>0</restock>
        <saferadius>500</saferadius>
        <distanceradius>500</distanceradius>
        <cleanupradius>200</cleanupradius>
        <flags deletable="0" init_random="0" remove_damaged="1"/>
        <position>fixed</position>
        <limit>mixed</limit>
        <active>0</active>
        <children>
            <child lootmax="0" lootmin="0" max="5" min="3" type="Old_Bike"/>
            <child lootmax="0" lootmin="0" max="5" min="3" type="Old_Bike_Red"/>
        </children>
    </event>
</events>
"""

ECONOMYCORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<economycore>
    <classes/>
    <defaults/>
    <economy/>
    <ce folder="custom">
        <file name="bikes-types.xml" type="types"/>
        <file name="bikes-events.xml" type="events"/>
    </ce>
</economycore>
"""

# Two positions for the bicycle event, four for the sedan: fewer than the test
# asks for in both cases, which is the warning B1 specifies.
EVENTSPAWNS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<eventposdef>
    <event name="VehicleBicycle">
        <pos x="100.0" z="200.0" a="0.0"/>
        <pos x="300.0" z="400.0" a="0.0"/>
    </event>
    <event name="VehicleCivilianSedan">
        <pos x="500.0" z="600.0" a="0.0"/>
    </event>
</eventposdef>
"""


@pytest.fixture()
def env(sample_config, tmp_mission_dir, monkeypatch):
    mission = tmp_mission_dir
    (mission / "db" / "types.xml").write_text(types_xml(), encoding="utf-8")
    (mission / "db" / "events.xml").write_text(events_xml(), encoding="utf-8")
    (mission / "cfgeconomycore.xml").write_text(ECONOMYCORE, encoding="utf-8")
    (mission / "cfgeventspawns.xml").write_text(EVENTSPAWNS, encoding="utf-8")
    custom = mission / "custom"
    custom.mkdir()
    (custom / "bikes-types.xml").write_text(MOD_TYPES, encoding="utf-8")
    (custom / "bikes-events.xml").write_text(MOD_EVENTS, encoding="utf-8")

    # The quick-tweak sidecars: the mission-root one and the env/ one.
    (mission / ".zsm-tweaks.json").write_text(
        json.dumps({"fields": {"db/types.xml": {"SparkPlug": {"nominal": 60}}}}),
        encoding="utf-8",
    )
    (mission / "env").mkdir(exist_ok=True)
    (mission / "env" / ".animal_tweaks.json").write_text(
        json.dumps({"wolves": 2.0}), encoding="utf-8"
    )

    storage = mission / "storage_1"
    (storage / "data").mkdir(parents=True)
    (storage / "players.db").write_bytes(b"players-before")
    (storage / "data" / "dynamic_0.bin").write_bytes(b"world-before")

    monkeypatch.setattr(
        "app.modmgr.mod_conflict.extract_mod_classnames_cached",
        lambda server_dir, mods, *a, **k: {m: list(CLASSES) for m in mods},
    )
    monkeypatch.setattr("app.ops.vps_readiness.mem_mb", lambda: (8192, 0))
    return sample_config, mission


def _types(mission, rel):
    from app.xml.types_xml import parse_types_file

    return {e.name: (e.nominal, e.min) for e in parse_types_file(mission / rel).entries}


def _events(mission, rel):
    from app.xml.events_xml import parse_events_file

    return {
        e.name: (
            e.nominal,
            e.min,
            e.max,
            e.active,
            tuple((c.type, c.min, c.max) for c in e.children),
        )
        for e in parse_events_file(mission / rel).entries
    }


def _tree(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── What each scope touches ─────────────────────────────────────────────


def test_loot_scope_zeroes_every_type_but_the_one_under_test(env):
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 25, "loot")

    modded = _types(mission, "custom/bikes-types.xml")
    assert modded["Old_Bike"] == (25, 25)
    assert modded["Old_Bike_Red"] == (0, 0)
    assert set(_types(mission, "db/types.xml").values()) == {(0, 0)}


def test_loot_scope_leaves_every_event_alone(env):
    """A vehicle event still spawning its cars is fine when the class under test
    is loot — and zeroing them would be a bigger change than was asked for."""
    config, mission = env
    before = _events(mission, "db/events.xml")
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    assert _events(mission, "db/events.xml") == before
    assert _events(mission, "custom/bikes-events.xml")["VehicleBicycle"][:4] == (6, 3, 9, False)


def test_loot_vehicles_zeroes_vehicle_events_and_leaves_the_rest(env):
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot_vehicles")

    stock = _events(mission, "db/events.xml")
    assert stock["VehicleCivilianSedan"][:3] == (0, 0, 0)
    assert stock["VehicleBoat"][:3] == (0, 0, 0)
    # Static* is a wreck, not a vehicle event (plan 35's EventTarget note), and
    # Infected* is not in this scope at all.
    assert stock["StaticBoatFishing"][:3] == (15, 0, 0)
    assert stock["InfectedArmy"][:3] == (50, 25, 250)


def test_everything_scope_also_zeroes_infected(env):
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "everything")

    stock = _events(mission, "db/events.xml")
    assert stock["InfectedArmy"][:3] == (0, 0, 0)
    assert stock["StaticBoatFishing"][:3] == (0, 0, 0)


def test_the_event_that_spawns_the_class_is_raised_and_switched_on(env):
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot_vehicles")

    nominal, minimum, maximum, active, children = _events(
        mission, "custom/bikes-events.xml"
    )["VehicleBicycle"]
    assert (nominal, minimum, maximum) == (10, 10, 10)
    assert active is True  # it shipped disabled; the test is the point of it
    assert children == (("Old_Bike", 0, 10), ("Old_Bike_Red", 0, 0))


def test_an_event_that_spawns_the_class_is_raised_whatever_its_name(env):
    """The scope decides what gets *zeroed*. An event that spawns X is raised
    in every event scope, or "Only X" would switch off the one event that
    matters whenever it isn't named Vehicle*."""
    config, mission = env
    (mission / "custom" / "bikes-events.xml").write_text(
        MOD_EVENTS.replace("VehicleBicycle", "AmbientBicycle"), encoding="utf-8"
    )
    st.start_only_x(config, MOD, "Old_Bike", 7, "loot_vehicles")
    assert _events(mission, "custom/bikes-events.xml")["AmbientBicycle"][:3] == (7, 7, 7)


def test_a_spawnabletypes_file_in_custom_is_left_alone(env):
    """Its <type> entries are attachment lists, not economy entries. Read as a
    types file, the class under test got a <nominal> and <min> inserted into
    Expansion's spawnabletypes on the first run against a real server."""
    config, mission = env
    path = mission / "custom" / "bikes-spawnabletypes.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<spawnabletypes>\n"
        '    <type name="Old_Bike">\n'
        '        <attachments chance="1.00">\n'
        '            <item name="BikeWheel" chance="0.80"/>\n'
        "        </attachments>\n"
        "    </type>\n"
        "</spawnabletypes>\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot_vehicles")
    assert path.read_bytes() == before


def test_lifetime_and_flags_are_never_touched(env):
    """The question is whether the mod's own entry spawns, not whether a
    rewritten one does."""
    from app.xml.types_xml import parse_types_file

    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "everything")
    entry = next(
        e
        for e in parse_types_file(mission / "custom" / "bikes-types.xml").entries
        if e.name == "Old_Bike"
    )
    assert entry.lifetime == 3888000
    assert entry.restock == 0
    assert entry.cost == 100
    assert entry.flags["count_in_map"] == 1
    assert entry.category == "vehiclesparts"


def test_a_types_entry_spelled_differently_from_the_class_still_matches(env):
    """DayZ resolves class names case-insensitively, and a mod whose types.xml
    disagrees with its config.cpp about case is common. Matching on the exact
    string would zero that mod's own entry along with everything else — a test
    that proves the mod is broken when it is the comparison that is.

    The argument's case is not what is being tested: `_validate_classname`
    already normalises that to the mod's own spelling. The *file's* is.
    """
    config, mission = env
    (mission / "custom" / "bikes-types.xml").write_text(
        MOD_TYPES.replace('name="Old_Bike"', 'name="OLD_BIKE"', 1), encoding="utf-8"
    )
    st.start_only_x(config, MOD, "Old_Bike", 5, "loot")
    assert _types(mission, "custom/bikes-types.xml")["OLD_BIKE"] == (5, 5)


def test_an_event_child_spelled_differently_still_matches(env):
    """Same on the events side: the child's type attribute is a class name."""
    config, mission = env
    (mission / "custom" / "bikes-events.xml").write_text(
        MOD_EVENTS.replace('type="Old_Bike"', 'type="old_bike"'), encoding="utf-8"
    )
    st.start_only_x(config, MOD, "Old_Bike", 9, "loot_vehicles")
    nominal, _min, _max, _active, children = _events(
        mission, "custom/bikes-events.xml"
    )["VehicleBicycle"]
    assert nominal == 9
    assert children == (("old_bike", 0, 9), ("Old_Bike_Red", 0, 0))


# ── Refusals ────────────────────────────────────────────────────────────


def test_a_class_with_no_economy_entry_is_refused_and_writes_nothing(env):
    config, mission = env
    before = _types(mission, "db/types.xml")

    with pytest.raises(st.SpawnTestError) as excinfo:
        st.start_only_x(config, MOD, "Bicycle_Base", 10, "loot_vehicles")
    assert excinfo.value.status == 422
    assert "no types.xml entry, and no event spawns it" in excinfo.value.message
    assert "Spawn next to me" in excinfo.value.message

    assert _types(mission, "db/types.xml") == before
    assert st.active_test(config) is None
    # Refused before the snapshot, so storage_1 was never emptied either.
    assert (mission / "storage_1" / "players.db").read_bytes() == b"players-before"


def test_the_loot_scope_refusal_does_not_mention_events(env):
    config, _ = env
    with pytest.raises(st.SpawnTestError) as excinfo:
        st.start_only_x(config, MOD, "Bicycle_Base", 10, "loot")
    assert "no types.xml entry — the economy" in excinfo.value.message


def test_an_unknown_scope_is_refused(env):
    config, _ = env
    with pytest.raises(st.SpawnTestError) as excinfo:
        st.start_only_x(config, MOD, "Old_Bike", 10, "vehicles")
    assert excinfo.value.status == 422
    assert st.active_test(config) is None


def test_bad_class_and_count_are_refused_the_same_way_as_package_a(env):
    config, _ = env
    for kwargs, message in (
        (dict(classname="Nope", count=10), "is not a class in"),
        (dict(classname="Old_Bike", count=-1), "whole number"),
    ):
        with pytest.raises(st.SpawnTestError) as excinfo:
            st.start_only_x(config, MOD, kwargs["classname"], kwargs["count"], "loot")
        assert message in excinfo.value.message
    assert st.active_test(config) is None


def test_a_second_test_is_refused(env):
    config, _ = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    with pytest.raises(st.SpawnTestError) as excinfo:
        st.start_only_x(config, MOD, "Old_Bike_Red", 10, "loot")
    assert "already active" in excinfo.value.message


# ── Spawn positions ─────────────────────────────────────────────────────


def test_too_few_positions_is_said_before_the_server_starts(env):
    config, _ = env
    lines: list[str] = []
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot_vehicles", lines.append)
    assert any(
        "VehicleBicycle has only 2 spawn positions — at most 2 can appear." in line
        for line in lines
    )


def test_no_positions_at_all_is_said_too(env):
    config, mission = env
    (mission / "cfgeventspawns.xml").write_text(
        EVENTSPAWNS.replace('<event name="VehicleBicycle">', '<event name="Unused">'),
        encoding="utf-8",
    )
    lines: list[str] = []
    st.start_only_x(config, MOD, "Old_Bike", 4, "loot_vehicles", lines.append)
    assert any("has no positions in cfgeventspawns.xml" in line for line in lines)


def test_positions_are_not_consulted_in_the_loot_scope(env):
    config, _ = env
    lines: list[str] = []
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot", lines.append)
    assert not any("spawn positions" in line for line in lines)


# ── storage_1 ───────────────────────────────────────────────────────────


def test_storage_is_emptied_on_start_and_put_back_on_end(env):
    """Loot already on the map counts toward the nominals it came from, so the
    "only X" world never appears while storage_1 survives."""
    config, mission = env
    storage = mission / "storage_1"
    before = _tree(storage)

    lines: list[str] = []
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot", lines.append)
    assert storage.is_dir() and _tree(storage) == {}
    assert any("storage_1 emptied for the test" in line for line in lines)

    (storage / "players.db").write_bytes(b"during-the-test")
    st.end_test(config)
    assert _tree(storage) == before


def test_the_storage_emptied_is_the_one_instance_id_names(env):
    """With instanceId = 3 the world is storage_3. Emptying storage_1 instead
    would leave the test running in the real world, with no copy of it."""
    config, mission = env
    set_instance_id(config.server_dir_path, 3)
    stale = mission / "storage_1"
    live = mission / "storage_3"
    (live / "data").mkdir(parents=True)
    (live / "data" / "dynamic_0.bin").write_bytes(b"live-world")
    stale_before, live_before = _tree(stale), _tree(live)

    lines: list[str] = []
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot", lines.append)
    assert live.is_dir() and _tree(live) == {}
    assert _tree(stale) == stale_before
    assert any("storage_3 emptied for the test" in line for line in lines)

    (live / "players.db").write_bytes(b"during-the-test")
    st.end_test(config)
    assert _tree(live) == live_before
    assert _tree(stale) == stale_before


def test_a_mission_without_storage_keeps_not_having_one(env):
    import shutil

    config, mission = env
    shutil.rmtree(mission / "storage_1")
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    assert st.active_test(config)["storage_existed"] is False
    (mission / "storage_1").mkdir()
    assert st.end_test(config)["storage"] == "removed"
    assert not (mission / "storage_1").exists()


# ── End: restore, sidecars, drift ───────────────────────────────────────


def test_end_puts_every_written_file_back_byte_for_byte(env):
    config, mission = env
    before = {
        rel: (mission / rel).read_bytes()
        for rel in ("db/types.xml", "db/events.xml", "custom/bikes-types.xml",
                    "custom/bikes-events.xml")
    }
    st.start_only_x(config, MOD, "Old_Bike", 10, "everything")
    assert (mission / "db" / "types.xml").read_bytes() != before["db/types.xml"]

    result = st.end_test(config)
    assert result["drifted"] == []
    assert "db/types.xml" in result["restored_files"]
    for rel, content in before.items():
        assert (mission / rel).read_bytes() == content, rel
    assert st.active_test(config) is None


def test_the_tweak_sidecars_are_restored_with_the_files_they_describe(env):
    """A baseline that survived while db/types.xml was rolled back would
    re-baseline the next tweak off the test's zeros."""
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")

    (mission / ".zsm-tweaks.json").write_text('{"fields": {}}', encoding="utf-8")
    (mission / "env" / ".animal_tweaks.json").write_text("{}", encoding="utf-8")

    restored = st.end_test(config)["restored_files"]
    assert ".zsm-tweaks.json" in restored
    assert "env/.animal_tweaks.json" in restored
    assert json.loads((mission / ".zsm-tweaks.json").read_text())["fields"]
    assert json.loads((mission / "env" / ".animal_tweaks.json").read_text()) == {"wolves": 2.0}


def test_the_preset_sidecar_is_restored_too(env):
    """plans/50 C: presets scale db/types.xml from .zsm-presets.json, so the
    same disagreement would follow a restore without it."""
    config, mission = env
    (mission / ".zsm-presets.json").write_text('{"version": 1, "fields": {"db/types.xml": {}}}', encoding="utf-8")
    before = (mission / ".zsm-presets.json").read_bytes()
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")

    (mission / ".zsm-presets.json").write_text("{}", encoding="utf-8")

    restored = st.end_test(config)["restored_files"]
    assert ".zsm-presets.json" in restored
    assert (mission / ".zsm-presets.json").read_bytes() == before


def test_a_sidecar_that_did_not_exist_is_not_invented(env):
    config, mission = env
    (mission / ".zsm-tweaks.json").unlink()
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    st.end_test(config)
    assert not (mission / ".zsm-tweaks.json").exists()


def test_an_edit_during_the_test_is_kept_aside_not_thrown_away(env):
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    edited = mission / "custom" / "bikes-types.xml"
    edited.write_text(MOD_TYPES.replace("<nominal>12</nominal>", "<nominal>99</nominal>"),
                      encoding="utf-8")

    lines: list[str] = []
    result = st.end_test(config, lines.append)

    assert result["drifted"] == ["custom/bikes-types.xml"]
    # The pre-test file wins on disk: a half-zeroed economy is nobody's intent.
    assert _types(mission, "custom/bikes-types.xml")["Old_Bike"] == (12, 6)
    assert any("was edited during the test" in line for line in lines)
    assert st.active_test(config) is None


def test_the_drifted_copy_is_on_disk_in_the_session(env):
    config, mission = env
    state = st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    from pathlib import Path

    session = Path(state["session_dir"])
    (mission / "db" / "types.xml").write_text(types_xml(), encoding="utf-8")
    st.end_test(config)
    kept = session / "drifted" / "db" / "types.xml"
    assert kept.is_file()
    assert kept.read_text(encoding="utf-8") == types_xml()


def test_a_test_that_changed_nothing_can_still_be_ended(env):
    """An economy already matching what the test asks for writes no file, so
    the session's files/ tree is empty. That is a valid test, not a lost one —
    End must not read the empty directory as a destroyed session."""
    config, mission = env
    (mission / ".zsm-tweaks.json").unlink()
    (mission / "env" / ".animal_tweaks.json").unlink()
    zeroed = MOD_TYPES.replace("<nominal>12</nominal>", "<nominal>0</nominal>")
    zeroed = zeroed.replace("<nominal>8</nominal>", "<nominal>0</nominal>")
    zeroed = zeroed.replace("<min>6</min>", "<min>0</min>").replace("<min>4</min>", "<min>0</min>")
    (mission / "custom" / "bikes-types.xml").write_text(zeroed, encoding="utf-8")
    (mission / "db" / "types.xml").unlink()

    state = st.start_only_x(config, MOD, "Old_Bike", 0, "loot")
    assert state["written"] == {}
    assert st.end_test(config) == {"restored_files": [], "storage": "restored", "drifted": []}
    assert st.active_test(config) is None


def test_a_file_the_test_did_not_write_is_never_reported_as_drift(env):
    """Only the files with a recorded post-write hash can drift. The sidecars
    are snapshotted but not written, so editing one is a restore, not drift."""
    config, mission = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot")
    (mission / ".zsm-tweaks.json").write_text('{"fields": {}}', encoding="utf-8")
    assert st.end_test(config)["drifted"] == []


# ── State and the pipeline reminder ─────────────────────────────────────


def test_the_state_file_records_the_scope_and_the_hashes(env):
    config, mission = env
    state = st.start_only_x(config, MOD, "Old_Bike", 10, "loot_vehicles")
    on_disk = json.loads((st._active_path(config)).read_text())
    assert on_disk == state
    assert on_disk["mode"] == "only_x"
    assert on_disk["scope"] == "loot_vehicles"
    assert str(mission / "db" / "types.xml") in on_disk["written"]
    assert all(len(h) == 64 for h in on_disk["written"].values())


def test_the_pipeline_reminder_names_the_mode_and_the_merge_risk(env):
    import asyncio

    from app.ops.pipeline import PipelineContext, run_pipeline

    config, _ = env
    st.start_only_x(config, MOD, "Old_Bike", 10, "loot_vehicles")

    emitted: list[str] = []
    ctx = PipelineContext(config=config, emit=emitted.append, runner=None,
                          should_run=lambda k: True)
    asyncio.run(run_pipeline([], ctx))

    assert len(emitted) == 1
    assert "Only X, loot + vehicles" in emitted[0]
    assert "An XML merge during the test undoes it for mod items." in emitted[0]


# ── CLI ─────────────────────────────────────────────────────────────────


def _cli(args):
    from click.testing import CliRunner

    from app.commands.mods import mods_group

    return CliRunner().invoke(mods_group, ["spawn-test", *args])


def test_cli_only_x_round_trip(env, monkeypatch):
    config, mission = env
    write_test_mods(config, ['@Bikes'])
    config.save()
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    before = (mission / "db" / "types.xml").read_bytes()

    result = _cli(["start", "Bikes", "Old_Bike", "--count", "8",
                   "--mode", "only-x", "--scope", "loot-vehicles"])
    assert result.exit_code == 0, result.output
    assert "Only X armed" in result.output
    assert (mission / "db" / "types.xml").read_bytes() != before

    assert "[Only X, loot + vehicles]" in _cli(["status"]).output

    assert _cli(["end"]).exit_code == 0
    assert (mission / "db" / "types.xml").read_bytes() == before
