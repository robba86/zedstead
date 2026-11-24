"""The merge undo and `zsm run rollback-xml` (app/xml/xml_undo.py).

Both copied `<file>.backup` back. Those are written once, at the first merge,
so after a DayZ update a rollback brought back the old game version's files
(browser test 2026-11-17, F8: VehicleMotorbike and MotorbikeScript gone).
"""
from __future__ import annotations

import json

import pytest

import app.commands.run as run_mod
from app.xml import xml_undo

MISSION = "dayzOffline.chernarusplus"

_CORE_OLD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<economycore>\n'
    '\t<rootclass name="CarScript" act="car"/>\n</economycore>\n'
)
_SPAWNS_NEW = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<eventposdef>\n'
    '\t<event name="VehicleMotorbike">\n\t\t<pos x="1.000000" z="2.000000" a="0"/>\n\t</event>\n'
    "</eventposdef>\n"
)
_SPAWNS_MERGED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<eventposdef>\n'
    '\t<event name="VehicleMotorbike">\n\t\t<pos x="1.000000" z="2.000000" a="0"/>\n\t</event>\n'
    "\t<!-- START MOD: @Bikes -->\n"
    '\t<event name="StaticBikes">\n\t\t<pos x="5.000000" z="6.000000" a="0"/>\n\t</event>\n'
    "\t<!-- END MOD: @Bikes -->\n"
    "</eventposdef>\n"
)
_CORE_MERGED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<economycore>\n'
    '\t<rootclass name="MotorbikeScript" act="car"/>\n'
    '\t<ce folder="custom">\n\t\t<file name="bikes-types.xml" type="types"/>\n\t</ce>\n'
    "</economycore>\n"
)


def _write(mission_dir, rel: str, text: str) -> None:
    path = mission_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(mission_dir, rel: str) -> str:
    return (mission_dir / rel).read_text(encoding="utf-8")


def test_the_undo_puts_back_the_files_from_before_the_merge_not_the_backup(
    sample_config, tmp_mission_dir
):
    # A `.backup` from an older game version, as the first merge left it.
    _write(tmp_mission_dir, "cfgeventspawns.xml.backup", "<eventposdef/>\n")
    _write(tmp_mission_dir, "cfgeconomycore.xml.backup", _CORE_OLD)
    # The files after this run's server download, before its merge.
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_NEW)
    _write(tmp_mission_dir, "cfgeconomycore.xml", _CORE_MERGED.replace(
        '\t<ce folder="custom">\n\t\t<file name="bikes-types.xml" type="types"/>\n\t</ce>\n', ""
    ))
    core_before = _read(tmp_mission_dir, "cfgeconomycore.xml")
    _write(tmp_mission_dir, "custom/old.xml", "<types/>\n")

    xml_undo.keep_before_merge(sample_config, MISSION)
    # The merge.
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_MERGED)
    _write(tmp_mission_dir, "cfgeconomycore.xml", _CORE_MERGED)
    (tmp_mission_dir / "custom" / "old.xml").unlink()
    _write(tmp_mission_dir, "custom/bikes-types.xml", "<types/>\n")
    xml_undo.record_after_merge(sample_config, MISSION)

    restored = xml_undo.undo_merge(sample_config, MISSION)

    assert _read(tmp_mission_dir, "cfgeventspawns.xml") == _SPAWNS_NEW
    assert _read(tmp_mission_dir, "cfgeconomycore.xml") == core_before
    assert sorted(p.name for p in (tmp_mission_dir / "custom").iterdir()) == ["old.xml"]
    assert "cfgeventspawns.xml" in restored
    assert not sample_config.paths.xml_undo_dir.exists()


def test_a_second_undo_or_another_mission_restores_nothing(sample_config, tmp_mission_dir):
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_NEW)
    with pytest.raises(xml_undo.NothingToUndo):
        xml_undo.undo_merge(sample_config, MISSION)

    xml_undo.keep_before_merge(sample_config, MISSION)
    xml_undo.record_after_merge(sample_config, MISSION)
    with pytest.raises(xml_undo.NothingToUndo):
        xml_undo.undo_merge(sample_config, "dayzOffline.enoch")
    xml_undo.undo_merge(sample_config, MISSION)
    with pytest.raises(xml_undo.NothingToUndo):
        xml_undo.undo_merge(sample_config, MISSION)


def test_a_manifest_name_outside_the_merge_targets_is_ignored(sample_config, tmp_mission_dir):
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_NEW)
    xml_undo.keep_before_merge(sample_config, MISSION)
    xml_undo.record_after_merge(sample_config, MISSION)
    undo_dir = sample_config.paths.xml_undo_dir
    manifest = json.loads((undo_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = ["../../evil.xml", "db/types.xml"]
    (undo_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write(tmp_mission_dir.parent.parent, "evil.xml", "kept\n")
    assert xml_undo.undo_merge(sample_config, MISSION) == []
    assert (tmp_mission_dir.parent.parent / "evil.xml").read_text(encoding="utf-8") == "kept\n"


def test_the_undo_refuses_a_file_changed_after_the_merge(sample_config, tmp_mission_dir):
    """An edit made after the update would otherwise go without a word."""
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_NEW)
    xml_undo.keep_before_merge(sample_config, MISSION)
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_MERGED)
    xml_undo.record_after_merge(sample_config, MISSION)
    edited = _SPAWNS_MERGED.replace('x="1.000000"', 'x="9.000000"')
    _write(tmp_mission_dir, "cfgeventspawns.xml", edited)

    with pytest.raises(xml_undo.ChangedSinceMerge, match="cfgeventspawns.xml changed"):
        xml_undo.undo_merge(sample_config, MISSION)
    assert _read(tmp_mission_dir, "cfgeventspawns.xml") == edited
    assert sample_config.paths.xml_undo_dir.exists()


def test_the_undo_refuses_a_merge_that_never_finished(sample_config, tmp_mission_dir):
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_NEW)
    xml_undo.keep_before_merge(sample_config, MISSION)
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_MERGED)
    with pytest.raises(xml_undo.ChangedSinceMerge, match="did not finish"):
        xml_undo.undo_merge(sample_config, MISSION)
    assert _read(tmp_mission_dir, "cfgeventspawns.xml") == _SPAWNS_MERGED


def test_the_pipeline_undo_fails_when_nothing_was_kept(sample_config, tmp_mission_dir):
    """It used to return quietly, and the rollback read as done."""
    from app.ops.pipeline import PipelineContext, _undo_merge_xml

    ctx = PipelineContext(
        config=sample_config, emit=lambda line: None, runner=None, should_run=lambda key: True,
    )
    with pytest.raises(xml_undo.NothingToUndo):
        _undo_merge_xml(ctx)


def test_rollback_xml_strips_the_mods_and_keeps_the_rest(sample_config, tmp_mission_dir, monkeypatch):
    from click.testing import CliRunner

    from app.commands.run import rollback_xml

    _write(tmp_mission_dir, "cfgeventspawns.xml.backup", "<eventposdef/>\n")
    _write(tmp_mission_dir, "cfgeventspawns.xml", _SPAWNS_MERGED)
    _write(tmp_mission_dir, "cfgeconomycore.xml", _CORE_MERGED)
    _write(tmp_mission_dir, "custom/bikes-types.xml", "<types/>\n")
    monkeypatch.setattr(run_mod, "load_context", lambda ctx: (sample_config, True))
    monkeypatch.setattr(run_mod, "confirm", lambda *a, **k: True)

    result = CliRunner().invoke(rollback_xml, [])

    assert result.exit_code == 0, result.output
    spawns = _read(tmp_mission_dir, "cfgeventspawns.xml")
    assert "VehicleMotorbike" in spawns and "StaticBikes" not in spawns
    core = _read(tmp_mission_dir, "cfgeconomycore.xml")
    assert "MotorbikeScript" in core and "bikes-types.xml" not in core
    assert list((tmp_mission_dir / "custom").iterdir()) == []
    assert "Removed 2" in result.output
