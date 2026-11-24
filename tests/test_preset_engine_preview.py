"""Tests for preset_engine.preview_preset accuracy.

These cover the regression where _preview_events / _preview_types /
_preview_gameplay reported "always-on" changes regardless of file state,
making a "closest preset" guess always pick Vanilla Balanced. Since
plans/50 C the preview is the apply's own plan, and the multipliers scale from
.zsm-presets.json rather than a .backup; the built-in presets' absolute
behaviour is tested in test_preset_absolute.py.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import write_xml

from app.presets.preset_engine import apply_preset, preview_preset
from app.presets.presets import (
    EventsLayer,
    GameplayJsonLayer,
    GlobalsLayer,
    PresetDefinition,
    ServerCfgLayer,
    TypesLayer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EVENTS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="InfectedArmy">
        <nominal>40</nominal>
        <min>20</min>
        <max>60</max>
        <lifetime>300</lifetime>
        <restock>0</restock>
        <saferadius>0</saferadius>
        <distanceradius>0</distanceradius>
        <cleanupradius>100</cleanupradius>
        <flags deletable="1" init_random="0" remove_damaged="0"/>
        <position>player</position>
        <limit>custom</limit>
        <active>1</active>
    </event>
    <event name="AnimalWolfPack">
        <nominal>10</nominal>
        <min>4</min>
        <max>12</max>
        <lifetime>600</lifetime>
        <restock>0</restock>
        <saferadius>0</saferadius>
        <distanceradius>0</distanceradius>
        <cleanupradius>100</cleanupradius>
        <flags deletable="1" init_random="0" remove_damaged="0"/>
        <position>player</position>
        <limit>custom</limit>
        <active>1</active>
    </event>
</events>
"""

TYPES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="AKM">
        <nominal>10</nominal>
        <lifetime>14400</lifetime>
        <restock>0</restock>
        <min>4</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
    </type>
</types>
"""


@pytest.fixture()
def events_mission(tmp_mission_dir: Path) -> Path:
    """Mission with a populated db/events.xml."""
    write_xml(tmp_mission_dir / "db" / "events.xml", EVENTS_XML)
    return tmp_mission_dir


@pytest.fixture()
def types_mission(tmp_mission_dir: Path) -> Path:
    """Mission with a populated db/types.xml."""
    write_xml(tmp_mission_dir / "db" / "types.xml", TYPES_XML)
    return tmp_mission_dir


def _backup(path: Path) -> Path:
    bp = path.with_suffix(path.suffix + ".backup")
    shutil.copy2(path, bp)
    return bp


def _make_preset(**kwargs) -> PresetDefinition:
    # Not built-in: a built-in is absolute and would also bring every stock
    # value, which these per-layer tests don't want to see.
    return PresetDefinition(
        name="Test",
        description="t",
        is_builtin=False,
        server_cfg=kwargs.get("server_cfg", ServerCfgLayer()),
        gameplay_json=kwargs.get("gameplay_json", GameplayJsonLayer()),
        events=kwargs.get("events", EventsLayer()),
        types=kwargs.get("types", TypesLayer()),
        globals=kwargs.get("globals", GlobalsLayer()),
    )


def _stub_config(server_cfg_path: Path) -> object:
    class _Cfg:
        def __init__(self, p): self.server_cfg_path = str(p)
    return _Cfg(server_cfg_path)


# ---------------------------------------------------------------------------
# events.xml
# ---------------------------------------------------------------------------

def test_events_no_change_when_already_at_multiplier(events_mission, tmp_server_dir):
    """The same multipliers applied already → preview reports no change."""
    preset = _make_preset(events=EventsLayer(infected_nominal=1.5, wildlife_nominal=0.8))
    config = _stub_config(tmp_server_dir / "serverDZ.cfg")
    apply_preset(config, preset, events_mission)

    pv = preview_preset(config, preset, events_mission)

    events_changes = [c for c in pv.changes if c.file == "events.xml"]
    assert events_changes == []


def test_events_reports_change_when_at_different_multiplier(events_mission, tmp_server_dir):
    """events.xml at 1.0x while preset wants 1.5x → preview reports change."""
    _backup(events_mission / "db" / "events.xml")  # backup == current == 1.0x

    preset = _make_preset(events=EventsLayer(infected_nominal=1.5, wildlife_nominal=1.5))
    pv = preview_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, events_mission)

    events_changes = [c for c in pv.changes if c.file == "events.xml"]
    assert len(events_changes) == 2


def test_events_no_backup_treats_current_as_baseline(events_mission, tmp_server_dir):
    """No backup + non-1.0 multiplier → reports change (would multiply current)."""
    preset = _make_preset(events=EventsLayer(infected_nominal=1.5))
    pv = preview_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, events_mission)

    assert any(c.file == "events.xml" for c in pv.changes)


# ---------------------------------------------------------------------------
# types.xml
# ---------------------------------------------------------------------------

def test_types_no_change_when_already_at_multiplier(types_mission, tmp_server_dir):
    """The same multipliers applied already → preview reports no change."""
    preset = _make_preset(types=TypesLayer(
        loot_nominal=2.0, loot_min=2.0, loot_lifetime=1.5, loot_restock=1.0,
    ))
    config = _stub_config(tmp_server_dir / "serverDZ.cfg")
    apply_preset(config, preset, types_mission)

    pv = preview_preset(config, preset, types_mission)

    types_changes = [c for c in pv.changes if c.file == "types.xml"]
    assert types_changes == []


def test_types_reports_change_when_at_different_multiplier(types_mission, tmp_server_dir):
    """types.xml at 1.0x while preset wants 2.0x → preview reports change."""
    _backup(types_mission / "db" / "types.xml")

    preset = _make_preset(types=TypesLayer(
        loot_nominal=2.0, loot_min=2.0, loot_lifetime=1.5, loot_restock=0.7,
    ))
    pv = preview_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, types_mission)

    # Three rows, not four: AKM's restock is 0, and 0 scaled is still 0 —
    # the preview lists only what changes.
    types_changes = [c for c in pv.changes if c.file == "types.xml"]
    assert [c.parameter for c in types_changes] == [
        "Loot nominal (1 item)", "Loot min (1 item)", "Loot lifetime (1 item)",
    ]


# ---------------------------------------------------------------------------
# cfggameplay.json
# ---------------------------------------------------------------------------

def test_gameplay_no_change_when_keys_already_present(tmp_mission_dir, tmp_server_dir):
    """cfggameplay.json contains the preset keys with matching values → no change."""
    gp_path = tmp_mission_dir / "cfggameplay.json"
    gp_path.write_text(json.dumps({
        "PlayerData": {
            "StaminaData": {"sprintStaminaModifierErc": 0.8},
            "DrowningData": {"staminaDepletionSpeed": 0.8},
        },
    }), encoding="utf-8")

    preset = _make_preset(gameplay_json=GameplayJsonLayer(
        PlayerData={"sprintStaminaModifierErc": 0.8, "staminaDepletionSpeed": 0.8},
    ))
    pv = preview_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, tmp_mission_dir)

    assert not any(c.file == "cfggameplay.json" for c in pv.changes)


def test_gameplay_reports_change_when_value_differs(tmp_mission_dir, tmp_server_dir):
    """cfggameplay.json key value differs → preview reports change."""
    gp_path = tmp_mission_dir / "cfggameplay.json"
    gp_path.write_text(json.dumps({
        "PlayerData": {"StaminaData": {"sprintStaminaModifierErc": 1.0}},
    }), encoding="utf-8")

    preset = _make_preset(gameplay_json=GameplayJsonLayer(
        PlayerData={"sprintStaminaModifierErc": 0.8},
    ))
    pv = preview_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, tmp_mission_dir)

    assert any(c.file == "cfggameplay.json" for c in pv.changes)


def test_gameplay_reports_change_when_file_missing(tmp_mission_dir, tmp_server_dir):
    """cfggameplay.json doesn't exist → preview reports change (defaults won't match)."""
    preset = _make_preset(gameplay_json=GameplayJsonLayer(
        PlayerData={"sprintStaminaModifierErc": 0.8},
        GeneralData={"disableRespawnDialog": True},
    ))
    pv = preview_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, tmp_mission_dir)

    assert any(c.file == "cfggameplay.json" for c in pv.changes)


def test_gameplay_refuses_a_file_it_cannot_read(tmp_mission_dir, tmp_server_dir):
    """A cfggameplay.json that is not JSON was read as Bohemia's defaults, and
    the preset then wrote them over the admin's file. It is refused now, and
    the file stays as it was."""
    from app.parsers.cfggameplay import GameplayFileError

    gp_path = tmp_mission_dir / "cfggameplay.json"
    gp_path.write_text('{"PlayerData": {"StaminaData": {"staminaMax": 150.0,}}}', encoding="utf-8")
    before = gp_path.read_bytes()
    preset = _make_preset(gameplay_json=GameplayJsonLayer(PlayerData={"sprintStaminaModifierErc": 0.8}))

    with pytest.raises(GameplayFileError, match="Raw tab"):
        apply_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, tmp_mission_dir)
    assert gp_path.read_bytes() == before


def test_gameplay_apply_changes_only_the_value_it_sets(tmp_mission_dir, tmp_server_dir):
    """Bohemia's file is tab-indented with inline lists; a preset used to
    rewrite all of it at four spaces."""
    gp_path = tmp_mission_dir / "cfggameplay.json"
    text = (
        '{\n\t"version": 123,\n\t"PlayerData":\n\t{\n\t\t"StaminaData":\n\t\t{\n'
        '\t\t\t"sprintStaminaModifierErc": 1.0,\n\t\t\t"staminaMax": 100.0\n\t\t}\n\t},\n'
        '\t"WorldsData":\n\t{\n\t\t"wetnessWeightModifiers": [1.0, 1.0, 1.33, 1.66, 2.0]\n\t}\n}'
    )
    gp_path.write_text(text, encoding="utf-8")
    preset = _make_preset(gameplay_json=GameplayJsonLayer(PlayerData={"sprintStaminaModifierErc": 0.8}))

    apply_preset(_stub_config(tmp_server_dir / "serverDZ.cfg"), preset, tmp_mission_dir)

    assert gp_path.read_text(encoding="utf-8") == text.replace(
        '"sprintStaminaModifierErc": 1.0', '"sprintStaminaModifierErc": 0.8'
    )
