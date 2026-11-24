"""Built-in presets are absolute and scale from a baseline that holds (plans/50 C).

F26: presets were overlays. Vanilla Balanced named only maxPlayers, so after
Hardcore it left first person, no crosshair, ZombieMaxCount 1200,
TimePenalty 600, the personal light off and loot at half. The multipliers
restored db/types.xml and db/events.xml from their .backup, which the map
editor rewrites and which throws away every edit made since.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from app.parsers.cfggameplay import read_cfggameplay
from app.parsers.servercfg import read_cfg_values
from app.presets.preset_engine import (
    PRESET_BASELINES,
    apply_preset,
    plan_preset,
    preview_preset,
)
from app.presets.presets import BUILTIN_PRESETS, STOCK_PRESET_VALUES, absolute
from app.xml.events_xml import parse_events_file
from app.xml.globals_xml import parse_globals_file
from app.xml.types_xml import parse_types_file, save_types_files, set_numeric_field
from tests.conftest import dayz_docs
from tests.test_quick_tweaks import events_xml, types_xml

STOCK_MAPS = ("dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal")

WOLF_EVENT = """    <event name="AnimalWolf">
        <nominal>8</nominal>
        <min>4</min>
        <max>10</max>
        <lifetime>180</lifetime>
        <restock>0</restock>
        <saferadius>200</saferadius>
        <distanceradius>0</distanceradius>
        <cleanupradius>0</cleanupradius>
        <flags deletable="0" init_random="0" remove_damaged="1"/>
        <position>fixed</position>
        <limit>custom</limit>
        <active>1</active>
        <children>
            <child lootmax="0" lootmin="0" max="1" min="1" type="Animal_CanisLupus_Grey"/>
        </children>
    </event>
</events>"""

SERVER_CFG = """hostname = "Test Server";
maxPlayers = 60;
thirdPersonView = 1;
disableCrosshair = 0;
respawnTime = 5;
enableCfgGameplayFile = 1;
class Missions
{
    class DayZ
    {
        template = "dayzOffline.chernarusplus";
    };
};
"""

MOD_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<types>
    <type name="ModRifle">
        <nominal>10</nominal>
        <lifetime>3600</lifetime>
        <restock>0</restock>
        <min>5</min>
    </type>
</types>
"""


@pytest.fixture()
def mission(sample_config, tmp_mission_dir: Path) -> Path:
    """A mission with Bohemia's own globals.xml and cfggameplay.json, and the
    stock types/events excerpts the Quick Tweaks tests use."""
    stock = dayz_docs("vanilla", "dayzOffline.chernarusplus")
    shutil.copy2(stock / "db" / "globals.xml", tmp_mission_dir / "db" / "globals.xml")
    shutil.copy2(stock / "cfggameplay.json", tmp_mission_dir / "cfggameplay.json")
    (tmp_mission_dir / "db" / "types.xml").write_text(types_xml(), encoding="utf-8")
    (tmp_mission_dir / "db" / "events.xml").write_text(
        events_xml().replace("</events>", WOLF_EVENT), encoding="utf-8",
    )
    sample_config.server_cfg_path.write_text(SERVER_CFG, encoding="utf-8")
    return tmp_mission_dir


def _apply(config, mission: Path, preset_id: str) -> list[str]:
    return apply_preset(config, BUILTIN_PRESETS[preset_id], mission)


def _types(mission: Path) -> dict[str, tuple]:
    return {
        e.name: (e.nominal, e.min, e.lifetime, e.restock)
        for e in parse_types_file(mission / "db" / "types.xml").entries
    }


def _events(mission: Path) -> dict[str, tuple]:
    return {e.name: (e.nominal, e.min) for e in parse_events_file(mission / "db" / "events.xml").entries}


def _managed_state(config, mission: Path) -> dict:
    """Every managed key's value on disk, in the stock table's shape."""
    stock = STOCK_PRESET_VALUES
    cfg_keys = [k for k, v in vars(stock.server_cfg).items() if v is not None]
    gameplay = read_cfggameplay(mission / "cfggameplay.json")
    player, general = gameplay["PlayerData"], gameplay["GeneralData"]
    globals_ = {e.name: e.value for e in parse_globals_file(mission / "db" / "globals.xml").entries}
    return {
        "cfg": read_cfg_values(config.server_cfg_path, cfg_keys),
        "globals": {k: globals_.get(k) for k in stock.globals.values},
        "light": player["disablePersonalLight"],
        "stamina": (player["StaminaData"]["sprintStaminaModifierErc"],
                    player["StaminaData"]["sprintStaminaModifierCro"]),
        "drowning": dict(player["DrowningData"]),
        "general": {k: general[k] for k in stock.gameplay_json.GeneralData},
    }


def _stock_state() -> dict:
    stock = STOCK_PRESET_VALUES
    return {
        "cfg": {k: str(v) for k, v in vars(stock.server_cfg).items() if v is not None},
        "globals": dict(stock.globals.values),
        "light": stock.gameplay_json.PlayerData["disablePersonalLight"],
        "stamina": (stock.gameplay_json.PlayerData["sprintStaminaModifierErc"],
                    stock.gameplay_json.PlayerData["sprintStaminaModifierCro"]),
        "drowning": {k: stock.gameplay_json.PlayerData[k]
                     for k in ("staminaDepletionSpeed", "healthDepletionSpeed", "shockDepletionSpeed")},
        "general": dict(stock.gameplay_json.GeneralData),
    }


# ---------------------------------------------------------------------------
# The finding's own sequence
# ---------------------------------------------------------------------------

def test_hardcore_then_vanilla_puts_every_managed_key_back(sample_config, mission):
    types_before, events_before = _types(mission), _events(mission)
    assert _managed_state(sample_config, mission) == _stock_state()

    _apply(sample_config, mission, "hardcore_survival")
    assert read_cfg_values(sample_config.server_cfg_path, ["thirdPersonView"]) == {"thirdPersonView": "0"}
    assert _types(mission)["AKM"][0] != types_before["AKM"][0]

    _apply(sample_config, mission, "vanilla_balanced")
    assert _managed_state(sample_config, mission) == _stock_state()
    assert _types(mission) == types_before  # AKM nominal included
    assert _events(mission) == events_before
    # Every multiplier is back at its baseline, so there is nothing to remember.
    assert not (mission / PRESET_BASELINES).exists()


def test_beginner_after_hardcore_leaves_no_hardcore_key_behind(sample_config, mission):
    types_before = _types(mission)
    _apply(sample_config, mission, "hardcore_survival")
    _apply(sample_config, mission, "beginner_friendly")

    state = _managed_state(sample_config, mission)
    assert state["globals"]["TimePenalty"] == "20"  # Hardcore's 600, not named by Beginner
    assert state["light"] is False
    assert state["general"]["disableRespawnDialog"] is False
    assert state["cfg"]["thirdPersonView"] == "1"
    # Loot scales from the pre-Hardcore value, not from Hardcore's half.
    assert _types(mission)["AKM"][0] == round(types_before["AKM"][0] * 2.0)


def test_the_same_preset_twice_writes_nothing_the_second_time(sample_config, mission):
    for preset_id in BUILTIN_PRESETS:
        _apply(sample_config, mission, preset_id)
        files = sorted(p for p in mission.rglob("*") if p.is_file()) + [sample_config.server_cfg_path]
        before = {p: p.read_bytes() for p in files}

        assert _apply(sample_config, mission, preset_id) == [], preset_id
        assert preview_preset(sample_config, BUILTIN_PRESETS[preset_id], mission).changes == []
        assert {p: p.read_bytes() for p in files} == before, preset_id


def test_a_types_edit_between_two_presets_becomes_the_new_base(sample_config, mission):
    _apply(sample_config, mission, "hardcore_survival")

    path = mission / "db" / "types.xml"
    tf = parse_types_file(path, preserve_comments=True)
    akm = next(e for e in tf.entries if e.name == "AKM")
    set_numeric_field(akm, "nominal", 7)  # what the Types page does
    save_types_files([tf])

    _apply(sample_config, mission, "beginner_friendly")
    assert _types(mission)["AKM"][0] == 14  # 7 x 2.0
    _apply(sample_config, mission, "vanilla_balanced")
    assert _types(mission)["AKM"][0] == 7


def test_mod_types_files_are_never_touched(sample_config, mission):
    custom = mission / "custom"
    custom.mkdir()
    (custom / "types_mod.xml").write_text(MOD_TYPES, encoding="utf-8")
    before = (custom / "types_mod.xml").read_bytes()

    for preset_id in BUILTIN_PRESETS:
        _apply(sample_config, mission, preset_id)
        assert (custom / "types_mod.xml").read_bytes() == before, preset_id


def test_the_preview_lists_exactly_what_apply_writes(sample_config, mission):
    """F27: the preview left out the enableCfgGameplayFile write and custom/."""
    sample_config.server_cfg_path.write_text(
        SERVER_CFG.replace("enableCfgGameplayFile = 1", "enableCfgGameplayFile = 0"), encoding="utf-8",
    )
    labels = {
        "serverDZ.cfg": str(sample_config.server_cfg_path),
        "cfggameplay.json": str(mission / "cfggameplay.json"),
        "globals.xml": str(mission / "db" / "globals.xml"),
        "events.xml": str(mission / "db" / "events.xml"),
        "types.xml": str(mission / "db" / "types.xml"),
    }
    first = preview_preset(sample_config, BUILTIN_PRESETS["hardcore_survival"], mission)
    assert "cfggameplay.json" in first.files_affected
    assert ("serverDZ.cfg", "enableCfgGameplayFile", "1") in {
        (c.file, c.parameter, c.new_value) for c in first.changes
    }

    for preset_id, preset in BUILTIN_PRESETS.items():
        preview = preview_preset(sample_config, preset, mission)
        rows = plan_preset(sample_config, preset, mission).changes
        written = apply_preset(sample_config, preset, mission)
        assert sorted(written) == sorted(labels[f] for f in preview.files_affected), preset_id
        assert [(c.file, c.parameter, c.new_value) for c in preview.changes] == [
            (c.file, c.parameter, c.new_value) for c in rows
        ]


def test_a_preset_from_an_older_zsm_is_mentioned_not_migrated(sample_config, mission):
    shutil.copy2(mission / "db" / "types.xml", mission / "db" / "types.xml.backup")
    preview = preview_preset(sample_config, BUILTIN_PRESETS["hardcore_survival"], mission)
    assert any("Restore from backup" in w for w in preview.warnings)

    _apply(sample_config, mission, "hardcore_survival")  # the sidecar now exists
    preview = preview_preset(sample_config, BUILTIN_PRESETS["beginner_friendly"], mission)
    assert not any("Restore from backup" in w for w in preview.warnings)
    # ...and the .backup itself is left where it was, for the editors and the merge.
    assert (mission / "db" / "types.xml.backup").is_file()


# ---------------------------------------------------------------------------
# The stock table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("map_dir", STOCK_MAPS)
def test_stock_values_match_bohemias_mission_files(map_dir):
    root = dayz_docs("vanilla", map_dir)
    globals_ = {e.name: e.value for e in parse_globals_file(root / "db" / "globals.xml").entries}
    for key, value in STOCK_PRESET_VALUES.globals.values.items():
        assert globals_[key] == value, (map_dir, key)

    gameplay = json.loads((root / "cfggameplay.json").read_text(encoding="utf-8"))
    player = gameplay["PlayerData"]
    nested = {**player, **player["StaminaData"], **player["DrowningData"]}
    for key, value in STOCK_PRESET_VALUES.gameplay_json.PlayerData.items():
        assert nested[key] == value and type(nested[key]) is type(value), (map_dir, key)
    for key, value in STOCK_PRESET_VALUES.gameplay_json.GeneralData.items():
        assert gameplay["GeneralData"][key] == value, (map_dir, key)


def test_stock_server_cfg_values_match_the_wiki():
    wiki = dayz_docs("wiki", "pages", "server-configuration.wiki").read_text(encoding="utf-8")
    for key, value in vars(STOCK_PRESET_VALUES.server_cfg).items():
        if value is None:
            continue
        found = re.search(rf"^{key}\s*=\s*(\d+);", wiki, re.M)
        assert found, key
        assert int(found.group(1)) == value, key


def test_every_key_a_builtin_sets_has_a_stock_value():
    stock = STOCK_PRESET_VALUES
    for preset_id, preset in BUILTIN_PRESETS.items():
        for key, value in vars(preset.server_cfg).items():
            assert value is None or getattr(stock.server_cfg, key) is not None, (preset_id, key)
        assert set(preset.globals.values) <= set(stock.globals.values), preset_id
        for section in ("PlayerData", "WorldsData", "GeneralData", "UIData", "MapData"):
            own = getattr(preset.gameplay_json, section) or {}
            assert set(own) <= set(getattr(stock.gameplay_json, section) or {}), (preset_id, section)
        for layer in ("events", "types"):
            for key, value in vars(getattr(preset, layer)).items():
                assert value is None or getattr(getattr(stock, layer), key) == 1.0, (preset_id, key)


def test_every_builtin_is_complete_once_resolved():
    stock = STOCK_PRESET_VALUES
    for preset_id, preset in BUILTIN_PRESETS.items():
        resolved = absolute(preset)
        assert set(resolved.globals.values) == set(stock.globals.values), preset_id
        for key, value in vars(stock.server_cfg).items():
            assert (getattr(resolved.server_cfg, key) is None) == (value is None), (preset_id, key)
        assert set(resolved.gameplay_json.PlayerData) == set(stock.gameplay_json.PlayerData), preset_id


def test_drowning_stamina_is_on_the_stock_scale():
    """Decision 4: stock is 10.0 per second while drowning. Hardcore's 1.3
    made drowning drain stamina about 8x slower than stock, not faster."""
    speeds = {
        preset_id: absolute(preset).gameplay_json.PlayerData["staminaDepletionSpeed"]
        for preset_id, preset in BUILTIN_PRESETS.items()
    }
    assert all(v >= 1.0 for v in speeds.values()), speeds
    assert speeds == {
        "beginner_friendly": 8.0, "vanilla_balanced": 10.0,
        "hardcore_survival": 13.0, "pvp_competitive": 6.0,
    }


def test_a_preset_takes_the_old_third_person_key_out(sample_config, mission):
    """A cfg from before DayZ 1.30 holds disable3rdPerson. A preset writes
    thirdPersonView and removes the old key (servercfg.REPLACED_CFG_KEYS), and
    the preview lists the removal as "(not set)"."""
    path = sample_config.server_cfg_path
    path.write_text(path.read_text().replace("thirdPersonView = 1;", "disable3rdPerson = 0;"))
    plan = plan_preset(sample_config, BUILTIN_PRESETS["hardcore_survival"], mission)
    rows = {c.parameter: (c.old_value, c.new_value) for w in plan.writes for c in w.changes if w.file == "serverDZ.cfg"}
    assert rows["disable3rdPerson"] == ("0", "(not set)")
    assert rows["thirdPersonView"] == ("(not set)", "0")
    _apply(sample_config, mission, "hardcore_survival")
    assert read_cfg_values(path, ["thirdPersonView", "disable3rdPerson"]) == {"thirdPersonView": "0", "disable3rdPerson": None}
