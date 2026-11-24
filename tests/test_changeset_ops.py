"""Tests for app/ops/changeset_ops.py — the start snapshot behind Analysis →
"Since last start".

A start zsm did not make (the scheduled restart task, systemd restarting a
crashed server) gets its snapshot afterwards, from zsm web's notifier
(plans/56 D).
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta

import pytest

from app.ops import changeset_ops
from app.shared.platform import IS_WINDOWS
from tests.conftest import write_test_ids


def _stamp_snapshot(config, when):
    changeset_ops.save_snapshot(config, changeset_ops.StartSnapshot(
        timestamp=when.isoformat(timespec="seconds"), mission="m", mods=[],
        server_cfg={}, file_hashes={},
    ))
    assert changeset_ops.load_snapshot(config).timestamp == when.isoformat(timespec="seconds")


def test_a_start_with_no_snapshot_gets_one(sample_config, monkeypatch):
    taken = []
    monkeypatch.setattr(changeset_ops, "take_start_snapshot", lambda config: taken.append(config))
    started = datetime.now().replace(microsecond=0)

    _stamp_snapshot(sample_config, started - timedelta(hours=5))  # the previous run's
    assert changeset_ops.snapshot_unrecorded_start(sample_config, started) is True
    assert taken == [sample_config]


def test_a_start_zsm_made_is_not_snapshotted_twice(sample_config, monkeypatch):
    taken = []
    monkeypatch.setattr(changeset_ops, "take_start_snapshot", lambda config: taken.append(config))
    started = datetime.now().replace(microsecond=0)

    for before_start in (timedelta(seconds=3), timedelta(seconds=-20)):
        _stamp_snapshot(sample_config, started - before_start)
        assert changeset_ops.snapshot_unrecorded_start(sample_config, started) is False
    assert changeset_ops.snapshot_unrecorded_start(sample_config, None) is False
    assert taken == []


def test_mod_ids_come_from_the_instance_being_diffed(sample_config, monkeypatch):
    """Not `DayzConfig.load()`, which in a panel serving several instances is
    the startup one's (plans/62 step 0)."""
    from app.ops import changeset_ops
    from app.shared.config import DayzConfig

    monkeypatch.setattr(DayzConfig, "load", classmethod(lambda cls, *a, **k: pytest.fail("loaded")))
    write_test_ids({'@OnlyHere': '424242'})

    assert changeset_ops._build_id_lookup(sample_config)["@OnlyHere"] == "424242"
    assert "@OnlyHere" not in changeset_ops._build_id_lookup(None)


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes")
def test_snapshots_are_written_owner_only_and_keep_the_values(sample_config):
    """A snapshot keeps serverDZ.cfg's passwords in clear for the change
    report, so it is 0600 (SEC-4). An older 0644 file is replaced, not
    rewritten in place, so its next save tightens it as well."""
    snap = changeset_ops.StartSnapshot(
        timestamp="2026-10-02T12:00:00", mission="m", mods=[],
        server_cfg={"password": "join-me", "passwordAdmin": "admin-pw"}, file_hashes={},
    )
    start = sample_config.paths.start_snapshot
    start.parent.mkdir(parents=True, exist_ok=True)
    start.write_text("{}", encoding="utf-8")
    os.chmod(start, 0o644)

    old = os.umask(0o022)
    try:
        changeset_ops.save_snapshot(sample_config, snap)
        changeset_ops.save_baseline(sample_config, snap)
    finally:
        os.umask(old)

    for path in (start, sample_config.paths.baseline_snapshot):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    assert changeset_ops.load_snapshot(sample_config).server_cfg["passwordAdmin"] == "admin-pw"
    assert changeset_ops.load_baseline(sample_config).server_cfg["password"] == "join-me"


# ---------------------------------------------------------------------------
# The mission: `template` inside class Missions { class DayZ }
# ---------------------------------------------------------------------------


def _changes(report, category):
    return [item.description for item in report.changes if item.category == category]


def test_the_snapshot_records_the_mission_dayz_loads(sample_config):
    """Read as a root key, `template` was None in every snapshot of a stock
    cfg, and a stray root-level one was taken for it."""
    cfg = sample_config.server_cfg_path
    cfg.write_text('template = "dayzOffline.enoch";\n' + cfg.read_text())

    snap = changeset_ops.capture_snapshot(sample_config)
    assert snap.server_cfg["template"] == "dayzOffline.chernarusplus"
    assert snap.mission == "dayzOffline.chernarusplus"


@pytest.mark.parametrize("before, after, row", [
    # No mission before, so there is no "Map changed" line to say it.
    ('hostname = "x";\n', "dayzOffline.sakhal", "  template: (empty) → dayzOffline.sakhal"),
    # Not a folder name, so `mission` is empty and only the row says it.
    (None, "../outside", "  template: dayzOffline.chernarusplus → ../outside"),
])
def test_a_mission_change_the_map_line_cannot_say_is_a_cfg_row(sample_config, before, after, row):
    from app.parsers.servercfg import set_mission_template

    cfg = sample_config.server_cfg_path
    if before is not None:
        cfg.write_text(before)
    changeset_ops.save_snapshot(sample_config, changeset_ops.capture_snapshot(sample_config))
    set_mission_template(cfg, after)

    report = changeset_ops.compute_changes(sample_config)
    assert _changes(report, "cfg") == [row]
    assert _changes(report, "mission") == []


def test_a_map_switch_is_reported_once(sample_config):
    from app.parsers.servercfg import set_mission_template

    changeset_ops.save_snapshot(sample_config, changeset_ops.capture_snapshot(sample_config))
    set_mission_template(sample_config.server_cfg_path, "dayzOffline.enoch")

    report = changeset_ops.compute_changes(sample_config)
    assert _changes(report, "mission") == ["Map changed: dayzOffline.chernarusplus → dayzOffline.enoch"]
    assert _changes(report, "cfg") == []


def test_a_snapshot_from_before_the_fix_shows_no_template_change(sample_config):
    """Such a snapshot holds `template: null` beside its mission. Read as is,
    every one of them (a baseline can be months old) would report
    `template: (empty) → <mission>` for a mission nobody changed."""
    old = changeset_ops.capture_snapshot(sample_config)
    old.server_cfg["template"] = None
    changeset_ops.save_baseline(sample_config, old)

    report = changeset_ops.compute_baseline_changes(sample_config)
    assert _changes(report, "cfg") == []
    assert _changes(report, "mission") == []


def test_no_storage_folder_is_scanned_whatever_its_number(sample_config, tmp_mission_dir):
    """Expansion keeps JSON inside the storage folder, and a server whose
    instanceId is 7 has it in storage_7."""
    (tmp_mission_dir / "cfggameplay.json").write_text("{}")
    for name in ("storage_1", "storage_7", "storage_-1"):
        (tmp_mission_dir / name / "expansion").mkdir(parents=True)
        (tmp_mission_dir / name / "expansion" / "garage.json").write_text("{}")
    (tmp_mission_dir / "storage_notes").mkdir()
    (tmp_mission_dir / "storage_notes" / "mine.json").write_text("{}")

    snap = changeset_ops.capture_snapshot(sample_config)
    mission_files = {rel for rel in snap.file_hashes if "storage" in rel or rel == "cfggameplay.json"}
    assert mission_files == {"cfggameplay.json", "storage_notes/mine.json"}


def test_every_change_category_has_a_web_label():
    """The Analysis > Config changes tab (now the Change history report) printed the raw ids (cfg_header, mods_add)
    as labels. A header heads a group; every other
    category needs a label in ChangeReport.jsx's CHANGE_LABELS."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ops = (root / "app/ops/changeset_ops.py").read_text(encoding="utf-8")
    page = (root / "webui/src/components/ChangeReport.jsx").read_text(encoding="utf-8")
    cats = set(re.findall(r'"([a-z_]+)"', " ".join(re.findall(r"ChangeItem\(\s*\"[a-z_]+\"", ops))))
    cats |= set(re.findall(r'"([a-z_]+_header)"', ops))
    body = re.search(r"const CHANGE_LABELS = \{(.*?)\n\}", page, re.S).group(1)
    keys = set(re.findall(r"^\s*([a-z_]+):", body, re.M))
    assert cats - keys == {c for c in cats if c.endswith("_header")}
