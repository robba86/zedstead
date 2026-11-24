"""Spawn-editor JSON state savers must write atomically.

The browser editor rewrites these mission configs (traders, safe zones,
garage) on every drag/edit, so a bare write_text truncated by a crash
mid-write would wipe the whole config. Each ``_save`` must go through
``atomic_write_text`` (temp file + os.replace), leaving no partial file and
preserving the original if the write fails. Since plans/54 C they reach it
through ``versioning.write_json``, which snapshots first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.spawn_editor.garage_state import GarageState
from app.spawn_editor.safezone_state import SafeZoneState
from app.spawn_editor.traders_state import TradersState

STATE_CLASSES = [TradersState, SafeZoneState, GarageState]


def _make_state(cls, config_path: Path, raw: dict):
    """Build a state object without running __init__ (which loads files)."""
    state = object.__new__(cls)
    state._config_path = config_path
    state._raw = raw
    return state


@pytest.mark.parametrize("cls", STATE_CLASSES)
def test_save_writes_content_and_leaves_no_temp_file(cls, tmp_path: Path):
    cfg = tmp_path / "config.json"
    raw = {"Items": [{"name": "x", "value": 1}]}
    state = _make_state(cls, cfg, raw)

    state._save()

    assert json.loads(cfg.read_text(encoding="utf-8")) == raw
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "config.json"]
    assert leftovers == []  # no .tmp file left behind


@pytest.mark.parametrize("cls", STATE_CLASSES)
def test_save_preserves_original_when_write_fails(cls, tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.json"
    original = {"Items": ["original"]}
    cfg.write_text(json.dumps(original), encoding="utf-8")

    state = _make_state(cls, cfg, {"Items": ["new", "data"]})

    # Simulate a crash partway through the atomic write.
    monkeypatch.setattr(
        "app.shared.json_patch.atomic_write_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError):
        state._save()

    # The original config is intact — not truncated or partially written.
    assert json.loads(cfg.read_text(encoding="utf-8")) == original
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "config.json"]
    assert leftovers == []


@pytest.mark.parametrize("cls", STATE_CLASSES)
def test_save_noop_without_config_path(cls, tmp_path: Path):
    state = _make_state(cls, None, {"Items": []})
    state._save()  # must not raise


def test_an_events_save_keeps_the_first_backup(tmp_mission_dir: Path):
    """events.xml.backup is create-only, like every other mission .backup:
    the map editor used to overwrite it on every save."""
    from app.spawn_editor.events_state import EventsState

    events = tmp_mission_dir / "db" / "events.xml"
    events.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<events>\n    <event name="VehicleBoat">\n        <nominal>2</nominal>\n    </event>\n</events>\n',
        encoding="utf-8",
    )
    backup = events.with_name("events.xml.backup")
    state = EventsState(tmp_mission_dir, None)
    state.load()
    state._save()
    assert backup.read_bytes() == events.read_bytes()

    backup.write_text("<events/>\n", encoding="utf-8")
    state._save()
    assert backup.read_text(encoding="utf-8") == "<events/>\n"


def test_a_player_spawn_save_keeps_the_first_backup(tmp_path: Path):
    """cfgplayerspawnpoints.xml.backup is create-only, like events.xml.backup."""
    from app.xml.playerspawnpoints_xml import backup_spawnpoints_file

    spawns = tmp_path / "cfgplayerspawnpoints.xml"
    spawns.write_text("<playerspawnpoints/>\n", encoding="utf-8")
    backup = backup_spawnpoints_file(spawns)
    assert backup.read_text(encoding="utf-8") == "<playerspawnpoints/>\n"

    spawns.write_text("<playerspawnpoints><fresh/></playerspawnpoints>\n", encoding="utf-8")
    backup_spawnpoints_file(spawns)
    assert backup.read_text(encoding="utf-8") == "<playerspawnpoints/>\n"
