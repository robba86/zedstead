"""Tests for app/ops/post_update.py: what happened to the server after an update (plans/73).

Nothing here starts a server or calls systemctl: `service_control` is patched,
and the server's log is a file the test writes into the profiles dir.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.ops import log_analysis, post_update
from tests.conftest import (
    disabled_names,
    private_fixture,
    set_instance_id,
    set_test_flags,
    write_test_ids,
    write_test_mods,
)


# The server's own output is in the private fixtures repository (plans/86 D2),
# read with conftest.private_fixture; without it these tests skip.
def ready_rpt() -> str:
    """Three lines of a 1.29 boot that took players (rpt/ready-1.29.RPT)."""
    return private_fixture("rpt/ready-1.29.RPT")


def loading_rpt() -> str:
    """The same boot before it took players: its first two lines."""
    return "".join(ready_rpt().splitlines(True)[:2])


# The set-aside fixtures replace `post_update.watch_boot`; this is the real one.
_REAL_WATCH_BOOT = post_update.watch_boot


@pytest.fixture()
def cfg(sample_config, tmp_path):
    sample_config.workshop_content_dir = str(
        tmp_path / "steam" / "steamapps" / "workshop" / "content" / "221100"
    )
    return sample_config


@pytest.fixture()
def unit(monkeypatch):
    """The server's unit as the boot watch sees it."""
    from app.server import service_control

    state = {"alive": True, "stats": {}}
    monkeypatch.setattr(service_control, "restart_stats", lambda config=None: dict(state["stats"]))
    monkeypatch.setattr(service_control, "is_server_process_alive", lambda config=None: state["alive"])
    return state


def _profiles(cfg):
    return log_analysis.profiles_dir(cfg)


def _write_rpt(cfg, name, text, *, age=0.0):
    path = _profiles(cfg) / name
    path.write_text(text, encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def _watch(cfg, since, **kw):
    kw.setdefault("poll", 0.01)
    kw.setdefault("timeout", 5)
    return asyncio.run(post_update.watch_boot(cfg, since, **kw))


# --- find_ready -----------------------------------------------------------


def test_find_ready_reads_only_whole_lines(tmp_path):
    log = tmp_path / "a.RPT"
    log.write_text("19:40:43 Version\n19:41:20 Player conn", encoding="utf-8")
    ready, offset = log_analysis.find_ready(log)
    assert not ready
    assert offset == len("19:40:43 Version\n")
    with open(log, "a", encoding="utf-8") as f:
        f.write("ect enabled\n")
    ready, offset = log_analysis.find_ready(log, offset)
    assert ready
    assert offset == log.stat().st_size


def test_find_ready_on_a_missing_file(tmp_path):
    assert log_analysis.find_ready(tmp_path / "gone.RPT", 7) == (False, 7)


# --- watch_boot -----------------------------------------------------------


def test_a_log_that_reaches_the_marker_is_joinable(cfg, unit):
    since = time.time() - 1
    rpt = _write_rpt(cfg, "DayZServer_x64_2026-10-03_19-40-43.RPT", ready_rpt())
    outcome = _watch(cfg, since)
    assert outcome.status == post_update.JOINABLE
    assert outcome.rpt == rpt


def test_a_log_that_stops_before_the_marker_times_out(cfg, unit):
    since = time.time() - 1
    _write_rpt(cfg, "DayZServer_x64_2026-10-03_19-40-43.RPT", loading_rpt())
    outcome = _watch(cfg, since, timeout=0.05)
    assert outcome.status == post_update.NOT_JOINABLE
    assert "not joinable" in outcome.detail


def test_a_log_from_before_the_start_is_not_this_boot(cfg, unit):
    """The previous run's log reached the marker; that says nothing about this boot."""
    since = time.time() - 1
    _write_rpt(cfg, "DayZServer_x64_2026-10-03_10-00-00.RPT", ready_rpt(), age=3600)
    outcome = _watch(cfg, since, timeout=0.05)
    assert outcome.status == post_update.NOT_JOINABLE
    assert outcome.rpt is None


def test_the_first_log_after_the_start_is_the_one_watched(cfg, unit):
    """Under Restart=always a crashing server writes one log per attempt."""
    since = time.time() - 10
    first = _write_rpt(cfg, "DayZServer_x64_2026-10-03_19-40-43.RPT", loading_rpt(), age=5)
    _write_rpt(cfg, "DayZServer_x64_2026-10-03_19-41-20.RPT", loading_rpt())
    assert post_update._boot_rpt(cfg, since) == first


def test_a_unit_in_auto_restart_is_a_crash(cfg, unit):
    """The step 0 crash: exit 139 four seconds in, then systemd's RestartSec wait."""
    since = time.time() - 1
    _write_rpt(cfg, "DayZServer_x64_2026-10-03_19-40-43.RPT", loading_rpt())
    unit["stats"] = {"looping": True, "count": 0}
    outcome = _watch(cfg, since)
    assert outcome.status == post_update.CRASHED
    assert "restarting" in outcome.detail
    assert outcome.rpt is not None


def test_a_crash_log_from_a_process_that_is_gone_is_a_crash(cfg, unit):
    """Step 0's compile failure: exit 139, so no start grace applies."""
    unit["alive"] = False
    since = time.time() - 1
    (_profiles(cfg) / "crash_2026-10-03_19-40-47.log").write_text("Can't compile\n", encoding="utf-8")
    outcome = _watch(cfg, since)
    assert outcome.status == post_update.CRASHED
    assert "crash_2026-10-03_19-40-47.log" in outcome.detail
    assert outcome.seconds < post_update._START_GRACE


def test_a_minidump_is_a_crash_while_the_process_runs(cfg, unit):
    since = time.time() - 1
    (_profiles(cfg) / "crash_2026-10-03_19-40-47.log").write_text("Can't compile\n", encoding="utf-8")
    (_profiles(cfg) / "DayZServer_x64_2026-10-03_19-40-47.mdmp").write_bytes(b"MDMP")
    assert post_update._crash_evidence(cfg, since, 0.0) == (
        "The server crashed (DayZServer_x64_2026-10-03_19-40-47.mdmp)."
    )


def test_a_crash_log_from_a_running_server_is_no_crash(cfg, unit):
    """The 1.30 boot: script errors for saved items it could not read went to
    a crash log 10 s before the server took players."""
    since = time.time() - 1
    (_profiles(cfg) / "crash_2026-10-16_18-43-36.log").write_text(load_errors_crash_log(), encoding="utf-8")
    assert post_update._crash_evidence(cfg, since, 0.0) == ""
    rpt = _write_rpt(cfg, "DayZServer_2026-10-16_18-43-32.RPT", ready_rpt())
    outcome = _watch(cfg, since)
    assert outcome.status == post_update.JOINABLE
    assert outcome.rpt == rpt


def test_an_old_crash_log_is_not_this_boot(cfg, unit):
    since = time.time() - 1
    old = _profiles(cfg) / "crash_2026-09-01_10-00-00.log"
    old.write_text("old\n", encoding="utf-8")
    stamp = time.time() - 86400
    os.utime(old, (stamp, stamp))
    assert post_update._crash_evidence(cfg, since, 0.0) == ""


def test_systemd_giving_up_is_a_crash(cfg, unit):
    unit["stats"] = {"limit_hit": True}
    assert "stopped restarting" in post_update._crash_evidence(cfg, time.time(), 0.0)


def test_a_missing_process_counts_only_after_the_grace(cfg, unit):
    unit["alive"] = False
    since = time.time()
    assert post_update._crash_evidence(cfg, since, 1.0) == ""
    assert "exited" in post_update._crash_evidence(cfg, since, post_update._START_GRACE)


def test_the_timeout_setting_has_a_floor(cfg):
    cfg.post_update_boot_timeout = 5
    assert post_update.boot_timeout(cfg) == 30
    cfg.post_update_boot_timeout = 900
    assert post_update.boot_timeout(cfg) == 900


# --- snapshot -------------------------------------------------------------


def _manifest(cfg, buildid, last_updated=1_790_000_000):
    steamapps = cfg.server_dir_path / "steamapps"
    steamapps.mkdir(exist_ok=True)
    (steamapps / "appmanifest_223350.acf").write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"223350"\n\t"buildid"\t\t"{buildid}"\n'
        f'\t"LastUpdated"\t\t"{last_updated}"\n}}\n',
        encoding="utf-8",
    )


def _workshop(cfg, times: dict[str, int]):
    acf = cfg.workshop_acf_path
    acf.parent.mkdir(parents=True, exist_ok=True)
    items = "".join(
        f'\t\t"{wid}"\n\t\t{{\n\t\t\t"size"\t\t"1"\n\t\t\t"timeupdated"\t\t"{ts}"\n\t\t}}\n'
        for wid, ts in times.items()
    )
    acf.write_text(
        f'"AppWorkshop"\n{{\n\t"appid"\t\t"221100"\n\t"WorkshopItemsInstalled"\n\t{{\n{items}\t}}\n}}\n',
        encoding="utf-8",
    )


def _mods(cfg, ids: dict[str, str]):
    # IDs first: a save gives each mod the ID zsm knows, and the list's own wins.
    write_test_ids(ids)
    write_test_mods(cfg, list(ids))


def test_snapshot_reads_the_build_and_the_enabled_mods(cfg):
    _manifest(cfg, "22222")
    _mods(cfg, {"@CF": "1559212036", "@Off": "1111"})
    _workshop(cfg, {"1559212036": 1_780_000_000, "1111": 1_770_000_000})
    set_test_flags(cfg, disabled=["@Off"])
    snap = post_update.snapshot(cfg)
    assert snap.build == "22222"
    assert snap.mods == {"@CF": 1_780_000_000}


def test_game_updated_compares_build_ids(cfg):
    _manifest(cfg, "22222")
    before = post_update.snapshot(cfg)
    assert not post_update.game_updated(before, cfg)
    _manifest(cfg, "33333")
    assert post_update.game_updated(before, cfg)


def test_a_first_install_is_not_a_game_update(cfg):
    before = post_update.snapshot(cfg)
    assert before.build == ""
    _manifest(cfg, "33333")
    assert not post_update.game_updated(before, cfg)


def test_mods_updated_lists_changed_and_new_mods(cfg):
    _mods(cfg, {"@CF": "1559212036", "@Same": "2222"})
    _workshop(cfg, {"1559212036": 100, "2222": 100})
    before = post_update.snapshot(cfg)
    _mods(cfg, {"@CF": "1559212036", "@Same": "2222", "@New": "3333"})
    _workshop(cfg, {"1559212036": 200, "2222": 100, "3333": 50})
    assert post_update.mods_updated(before, cfg) == ["@CF", "@New"]


# --- naming the mods (plans/73 step 2) --------------------------------------

# The step 0 boot (DayZ 1.29.163709, Linux, 2026 Q4): the header, the mod's
# PBO being mounted, and the script errors. The PBO prefix (Plan73Broken)
# differs from the folder (@Plan73BrokenScript) on purpose.
def step0_rpt() -> str:
    return private_fixture("rpt/step0-1.29.RPT")

# The first two entries of the crash log of a 1.30 boot that took players 10 s
# later (measured in a test container). Its world was saved by an earlier boot
# without @CF.
def load_errors_crash_log() -> str:
    return private_fixture("crash/load-errors-1.30.log")

# The step 0 boot's crash log.
def step0_crash_log() -> str:
    return private_fixture("crash/step0-1.29.log")


def _pbo(path, prefix: str | None, files=(("config.cpp", b"class CfgPatches {};"),)):
    """A PBO the way step 0 built one: a version entry whose header extension
    carries the prefix, the file entries, a terminator, the data, a checksum."""
    import hashlib
    import struct

    header = b"\0" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0)
    if prefix is not None:
        header += b"prefix\0" + prefix.encode() + b"\0"
    header += b"\0"
    for name, data in files:
        header += name.encode() + b"\0" + struct.pack("<5I", 0, len(data), 0, 0, len(data))
    header += b"\0" + struct.pack("<5I", 0, 0, 0, 0, 0)
    body = header + b"".join(data for _, data in files)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + b"\0" + hashlib.sha1(body).digest())
    return path


def _mod(cfg, folder, *prefixes):
    for i, prefix in enumerate(prefixes):
        _pbo(cfg.server_dir_path / folder / "addons" / f"part{i}.pbo", prefix)


def test_the_pbo_prefix_is_read_from_the_header(tmp_path):
    from app.modmgr.mod_conflict import _read_pbo_prefix

    assert _read_pbo_prefix(_pbo(tmp_path / "a.pbo", "Plan73Broken")) == "Plan73Broken"
    assert _read_pbo_prefix(_pbo(tmp_path / "b.pbo", "x\\JM\\CF\\Scripts\\")) == "x/JM/CF/Scripts"


def test_a_pbo_without_a_prefix_is_mounted_under_its_name(tmp_path):
    from app.modmgr.mod_conflict import _read_pbo_prefix

    assert _read_pbo_prefix(_pbo(tmp_path / "plain_scripts.pbo", None)) == "plain_scripts"


def test_a_file_that_is_not_a_pbo_has_no_prefix(tmp_path):
    from app.modmgr.mod_conflict import _read_pbo_prefix

    junk = tmp_path / "junk.pbo"
    junk.write_bytes(b"not a pbo at all")
    assert _read_pbo_prefix(junk) == ""
    assert _read_pbo_prefix(tmp_path / "missing.pbo") == ""


def test_prefixes_are_cached_per_mod_until_the_pbos_change(cfg, monkeypatch):
    from app.modmgr import mod_conflict

    _mod(cfg, "@A", "PrefA")
    assert mod_conflict.mod_pbo_prefixes(cfg.server_dir_path, ["@A"]) == {"@A": ["PrefA"]}
    calls = []
    real = mod_conflict._read_pbo_prefix
    monkeypatch.setattr(mod_conflict, "_read_pbo_prefix", lambda p: calls.append(p) or real(p))
    assert mod_conflict.mod_pbo_prefixes(cfg.server_dir_path, ["@A"]) == {"@A": ["PrefA"]}
    assert calls == []


def test_the_step0_errors_name_the_mod_by_its_prefix(cfg):
    """Both path shapes, the RPT and the crash log, one mod: @Plan73BrokenScript."""
    since = time.time() - 1
    _mod(cfg, "@Plan73BrokenScript", "Plan73Broken")
    rpt = _write_rpt(cfg, "DayZServer_2026-10-03_19-40-43.RPT", step0_rpt())
    (_profiles(cfg) / "crash_2026-10-03_19-40-45.log").write_text(step0_crash_log(), encoding="utf-8")

    found = post_update.suspects(cfg, rpt, since=since)

    assert [s.mod for s in found.suspects] == ["@Plan73BrokenScript"]
    lines = found.suspects[0].lines
    # Unique messages only: the RPT repeats one, the crash log repeats another.
    assert lines == [
        "19:40:47 SCRIPT    (E): @\"Plan73Broken/Scripts/4_World/plan73_broken.c,5\": Unknown type 'Plan73UndefinedType'",
        "19:40:47 SCRIPT    (E): @\"Plan73Broken/Scripts/4_World/plan73_broken.c,5\": Error in parameters",
        "Plan73Broken/Scripts/4_World/plan73_broken.c(5): Unknown type 'Plan73UndefinedType'",
    ]
    assert found.unattributed == []
    assert found.modules == ["World"]


def test_the_crash_log_alone_is_enough(cfg):
    since = time.time() - 1
    _mod(cfg, "@Plan73BrokenScript", "Plan73Broken")
    write_test_mods(cfg, ['@Plan73BrokenScript'])
    (_profiles(cfg) / "crash_2026-10-03_19-40-45.log").write_text(step0_crash_log(), encoding="utf-8")
    found = post_update.suspects(cfg, None, since=since)
    assert [s.mod for s in found.suspects] == ["@Plan73BrokenScript"]


def test_a_crash_logs_stack_names_the_mod_and_skips_the_game_and_the_mission(cfg):
    since = time.time() - 1
    _mod(cfg, "@CF", "JM/CF/Scripts")
    _mod(cfg, "@TraderPlus", "TraderPlus/scripts")
    write_test_mods(cfg, ["@CF", "@TraderPlus"])
    (_profiles(cfg) / "crash_2026-10-16_18-43-36.log").write_text(load_errors_crash_log(), encoding="utf-8")

    found = post_update.suspects(cfg, None, since=since)

    assert [s.mod for s in found.suspects] == ["@CF"]
    assert found.suspects[0].lines[0] == (
        "JM/CF/Scripts/3_Game/communityframework/communityframework.c:139 Function FormatError"
    )
    assert found.unattributed == []


@pytest.mark.parametrize("line, path", [
    ("JM/CF/Scripts/4_World/communityframework/entities/itembase.c:19 Function OnStoreLoad",
     "JM/CF/Scripts/4_World/communityframework/entities/itembase.c"),
    ("scripts/1_Core/proto/endebug.c:92 Function Error", None),
    ("$CurrentDir:mpmissions/dayzOffline.chernarusplus/init.c:6 Function main", None),
    ("Reason: Corrupt modstorage header, unsupported CF version (4)", None),
])
def test_a_stack_frame_names_its_file(line, path):
    assert post_update.script_path(line) == path


def test_the_longest_prefix_wins(cfg):
    _mod(cfg, "@Framework", "Shared")
    _mod(cfg, "@Addon", "Shared/Addon")
    rpt = _write_rpt(cfg, "x.RPT", (
        "== ./DayZServer -mod=@Framework;@Addon\n"
        '10:00:00 SCRIPT    (E): @"Shared/Addon/Scripts/4_World/a.c,3": Unknown type \'X\'\n'
        '10:00:00 SCRIPT    (E): @"Shared/Scripts/3_Game/b.c,9": Unknown type \'Y\'\n'
    ))
    found = post_update.suspects(cfg, rpt, since=time.time() - 1)
    assert {s.mod: len(s.lines) for s in found.suspects} == {"@Addon": 1, "@Framework": 1}


def test_a_path_no_mod_claims_is_reported_as_it_is(cfg):
    _mod(cfg, "@A", "PrefA")
    line = '10:00:00 SCRIPT    (E): @"scripts/4_World/entities/manbase.c,12": Unknown type \'Z\''
    rpt = _write_rpt(cfg, "x.RPT", "== ./DayZServer -mod=@A\n" + line + "\n")
    found = post_update.suspects(cfg, rpt, since=time.time() - 1)
    assert found.suspects == []
    assert found.unattributed == [line]


def test_two_mods_in_one_run(cfg):
    _mod(cfg, "@A", "PrefA")
    _mod(cfg, "@B", "PrefB")
    rpt = _write_rpt(cfg, "x.RPT", (
        "== ./DayZServer -mod=@A;@B\n"
        '10:00:00 SCRIPT    (E): @"PrefB/Scripts/4_World/b.c,1": Unknown type \'B\'\n'
        '10:00:00 SCRIPT    (E): @"PrefA/Scripts/4_World/a.c,1": Unknown type \'A\'\n'
    ))
    found = post_update.suspects(cfg, rpt, since=time.time() - 1)
    # In the order the log names them.
    assert [s.mod for s in found.suspects] == ["@B", "@A"]


def test_a_warning_is_not_a_suspect(cfg):
    _mod(cfg, "@A", "PrefA")
    rpt = _write_rpt(cfg, "x.RPT", (
        "== ./DayZServer -mod=@A\n"
        '10:00:00 SCRIPT    (W): @"PrefA/Scripts/4_World/a.c,1": Variable \'x\' is not used\n'
    ))
    assert post_update.suspects(cfg, rpt, since=time.time() - 1).suspects == []


def test_a_suspect_says_whether_it_predates_the_game_update(cfg):
    _manifest(cfg, "33333", last_updated=2_000)
    _mods(cfg, {"@Old": "1111", "@Fresh": "2222"})
    _workshop(cfg, {"1111": 1_000, "2222": 3_000})
    _mod(cfg, "@Old", "OldP")
    _mod(cfg, "@Fresh", "FreshP")
    rpt = _write_rpt(cfg, "x.RPT", (
        "== ./DayZServer -mod=@Old;@Fresh\n"
        '10:00:00 SCRIPT    (E): @"OldP/a.c,1": Unknown type \'A\'\n'
        '10:00:00 SCRIPT    (E): @"FreshP/b.c,1": Unknown type \'B\'\n'
    ))
    found = {s.mod: s for s in post_update.suspects(cfg, rpt, since=time.time() - 1).suspects}
    assert found["@Old"].before_game_update is True
    assert found["@Old"].workshop_id == "1111"
    assert found["@Fresh"].before_game_update is False


# --- the report (plans/73 step 3) -------------------------------------------


@pytest.fixture()
def discord(monkeypatch):
    from app.monitoring import discord_hooks

    sent: list[dict] = []
    monkeypatch.setattr(discord_hooks, "notify_boot_failed", lambda config, **kw: sent.append(kw))
    return sent


def _check(cfg, before, since, **kw):
    kw.setdefault("timeout", 5)
    return asyncio.run(post_update.check_boot(cfg, before, since=since, **kw))


def _fast(monkeypatch):
    """watch_boot polling every 10 ms instead of 3 s."""
    monkeypatch.setattr(post_update, "_POLL", 0.01)
    real = post_update.watch_boot

    async def _watch(config, since, **kw):
        kw["poll"] = 0.01
        return await real(config, since, **kw)

    monkeypatch.setattr(post_update, "watch_boot", _watch)


def test_a_crashed_boot_after_a_game_update_is_saved_and_sent_once(cfg, unit, discord, monkeypatch):
    _fast(monkeypatch)
    unit["alive"] = False  # step 0's exit 139
    _manifest(cfg, "100")
    _mods(cfg, {"@Plan73BrokenScript": "999"})
    before = post_update.snapshot(cfg)
    _manifest(cfg, "200")
    since = time.time() - 1
    _mod(cfg, "@Plan73BrokenScript", "Plan73Broken")
    _write_rpt(cfg, "DayZServer_2026-10-03_19-40-43.RPT", step0_rpt())
    (_profiles(cfg) / "crash_2026-10-03_19-40-45.log").write_text(step0_crash_log(), encoding="utf-8")
    lines: list[str] = []

    report = _check(cfg, before, since, emit=lines.append)

    assert report["outcome"] == post_update.CRASHED
    assert report["trigger"] == "game"
    assert (report["build_before"], report["build_after"]) == ("100", "200")
    assert [s["mod"] for s in report["suspects"]] == ["@Plan73BrokenScript"]
    assert post_update.load_report(cfg) == report
    assert len(discord) == 1
    assert discord[0]["suspects"] == ["@Plan73BrokenScript"]
    assert "after the DayZ update (build 100 → 200)" in discord[0]["occasion"]
    assert "DayZ updated: build 100 → 200." in lines
    assert "Suspect: @Plan73BrokenScript" in lines


def test_a_joinable_boot_is_saved_and_sends_nothing(cfg, unit, discord, monkeypatch):
    _fast(monkeypatch)
    before = post_update.snapshot(cfg)
    since = time.time() - 1
    _write_rpt(cfg, "x.RPT", ready_rpt())
    report = _check(cfg, before, since)
    assert report["outcome"] == post_update.JOINABLE
    assert report["suspects"] == []
    assert discord == []
    assert not post_update.failed(post_update.load_report(cfg))


def _damaged_rpt(items: int) -> str:
    """A boot that could not read *items* saved items, then took players. The
    item block is a 1.29 boot's (rpt/toxiczone-1.29.RPT, tests/test_log_analysis.py)."""
    log = private_fixture("rpt/toxiczone-1.29.RPT")
    start = log.index(' 8:41:18 !!! Scripted variables corrupted upon "GP5GasMask"')
    end = log.index("Entity will not be loaded correctly.\n", start) + len("Entity will not be loaded correctly.\n")
    block = log[start:end]
    return "19:40:43 Version 1.30.164078\n19:40:44 Mission read.\n" + block * items + "19:41:20 Player connect enabled\n"


def test_unreadable_saved_items_are_counted_from_the_boot_log(cfg):
    assert post_update.unreadable_items(_write_rpt(cfg, "a.RPT", _damaged_rpt(3))) == 3
    assert post_update.unreadable_items(_write_rpt(cfg, "b.RPT", ready_rpt())) == 0
    assert post_update.unreadable_items(None) == 0


@pytest.fixture()
def damage_sent(monkeypatch):
    from app.monitoring import discord_hooks

    sent: list[dict] = []
    monkeypatch.setattr(discord_hooks, "notify_world_damage", lambda config, **kw: sent.append(kw))
    return sent


def test_a_joinable_boot_that_lost_many_saved_items_warns_once(cfg, unit, discord, damage_sent, monkeypatch):
    """The 1.30 boot of a world an earlier boot without mods had saved."""
    _fast(monkeypatch)
    monkeypatch.setattr(post_update, "latest_backup", lambda config, since: {
        "name": "dayz-backup-x.zip", "timestamp": "2026-10-16T18:39:27", "fresh": True,
    })
    before = post_update.snapshot(cfg)
    since = time.time() - 1
    _write_rpt(cfg, "x.RPT", _damaged_rpt(post_update.WORLD_DAMAGE_ITEMS))
    lines: list[str] = []

    report = _check(cfg, before, since, emit=lines.append)

    assert report["outcome"] == post_update.JOINABLE
    assert (report["unreadable_items"], report["world_damage"]) == (100, True)
    assert report["backup"]["name"] == "dayz-backup-x.zip"
    assert discord == []
    assert damage_sent == [{"occasion": "after the update", "items": 100, "backup": "dayz-backup-x.zip"}]
    assert any(line.startswith("Warning: the server could not read 100 saved items") for line in lines)
    assert any("dayz-backup-x.zip" in line for line in lines)
    assert not post_update.failed(post_update.load_report(cfg))


def test_a_few_unreadable_items_after_a_mod_update_send_nothing(cfg, unit, discord, damage_sent, monkeypatch):
    _fast(monkeypatch)
    before = post_update.snapshot(cfg)
    since = time.time() - 1
    _write_rpt(cfg, "x.RPT", _damaged_rpt(post_update.WORLD_DAMAGE_ITEMS - 1))

    report = _check(cfg, before, since)

    assert (report["unreadable_items"], report["world_damage"], report["backup"]) == (99, False, None)
    assert damage_sent == []
    assert not any(line.startswith("Warning") for line in post_update.summary_lines(report))


def test_world_damage_without_a_backup_says_so():
    lines = post_update.damage_lines({"world_damage": True, "unreadable_items": 500, "backup": None})
    assert lines[1] == "No backup of this mission exists to restore."


def test_the_world_damage_message_escapes_the_backup_name(sample_config, monkeypatch):
    from app.monitoring import discord_hooks

    sent = []
    monkeypatch.setattr(discord_hooks, "_is_enabled", lambda config, kind: kind == "crash")
    monkeypatch.setattr(discord_hooks, "_send_embed", lambda url, embed: sent.append(embed))
    discord_hooks.notify_world_damage(sample_config, occasion="after the update", items=7, backup="a_*b*.zip")
    (embed,) = sent
    assert "could not read 7 saved items" in embed["description"]
    assert "a_*b*.zip" not in embed["description"]


def test_a_mod_only_update_is_worded_as_one(cfg):
    """plans/73 D2: the same triage, worded "after a mod update"."""
    report = {"trigger": "mods", "mods_updated": ["@A", "@B", "@C", "@D"]}
    assert post_update.occasion(report) == "after a mod update (@A, @B, @C and 1 more)"
    assert post_update.occasion({"trigger": ""}) == "after the update"


def test_a_failed_boot_with_no_script_path_says_where_to_look(cfg):
    lines = post_update.summary_lines({"outcome": "not_joinable", "detail": "x", "rpt": "a.RPT"})
    assert lines[-1] == "The server log names no script file (a.RPT); see Analysis → Server logs."


def test_a_start_that_fails_is_not_a_boot_report(cfg, monkeypatch):
    from app.server import service_control

    monkeypatch.setattr(service_control, "start_server", lambda config, anyway=False: service_control.ServiceResult(False, "no unit"))
    report = asyncio.run(post_update.start_and_check(cfg, post_update.UpdateSnapshot(1.0, "")))
    assert report == {"outcome": "not_started", "detail": "no unit"}
    assert post_update.load_report(cfg) is None


def test_discord_escapes_mod_names_and_fences_log_lines(cfg, monkeypatch):
    from app.monitoring import discord_hooks

    cfg.discord_webhook_url = "https://discord.example/hook"
    cfg.discord_hook_crash = True
    sent = []
    monkeypatch.setattr(discord_hooks, "_send_embed", lambda url, embed: sent.append(embed))
    discord_hooks.notify_boot_failed(
        cfg, occasion="after a mod update (@x_y)", detail="crashed",
        suspects=["@Evil*Mod"], lines=["SCRIPT (E): ```x```"],
    )
    desc = sent[0]["description"]
    assert "@Evil\\*Mod" in desc
    assert "@x\\_y" in desc
    assert "```x```" not in desc


# --- the offer (plans/73 step 4) ----------------------------------------------


def _failed_report(cfg, *mods, started=0.0):
    report = {
        "outcome": post_update.CRASHED, "started": started, "build_after": "200",
        "suspects": [{"mod": m, "lines": []} for m in mods],
        "set_aside": [], "recheck": None,
    }
    post_update.save_report(cfg, report)
    return report


@pytest.fixture()
def deps(monkeypatch):
    """The mod dependency graph: mod -> the mods it needs."""
    from app.modmgr import mod_conflict

    graph: dict[str, set[str]] = {}
    monkeypatch.setattr(mod_conflict, "get_mod_dependencies", lambda mods, server_dir, ids=None, **kw: graph)
    return graph


def test_a_framework_takes_its_dependents_along(cfg, deps):
    _mods(cfg, {"@CF": "1", "@Trader": "2", "@TraderAddon": "3", "@Other": "4"})
    deps.update({"@Trader": {"@CF"}, "@TraderAddon": {"@Trader"}})
    plan = post_update.offer(cfg, _failed_report(cfg, "@CF"))
    assert plan["mods"] == ["@CF"]
    assert plan["dependents"] == ["@Trader", "@TraderAddon"]
    assert "1,334" in plan["warning"]


def _addon(cfg, mod: str, addon: str, required: list[str]) -> None:
    from tests.test_mod_conflict import _build_pbo, _build_rap_config

    path = cfg.server_dir_path / mod / "addons" / f"{addon.lower()}.pbo"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_build_pbo({"config.bin": _build_rap_config(addon, required)}))


@pytest.mark.parametrize("framework, addon", [
    ("@CF", "JM_CF_Scripts"),
    ("@DayZ-Expansion-Core", "DayZExpansion_Core_Scripts"),
    ("@Framework", "Framework_Core"),
])
def test_a_mod_that_needs_cf_or_expansion_goes_with_it(cfg, framework, addon):
    """The load order's graph skips addon names that start like vanilla ones,
    which CF's (`JM_`) and Expansion's (`DayZ`) do. Workshop ids no catalog knows."""
    _mods(cfg, {framework: "9000000001", "@Needs": "9000000002", "@NeedsThat": "9000000003", "@Alone": "9000000004"})
    _addon(cfg, framework, addon, ["DZ_Data"])
    _addon(cfg, "@Needs", "Needs_Scripts", ["DZ_Data", addon])
    _addon(cfg, "@NeedsThat", "NeedsThat_Scripts", ["Needs_Scripts"])
    _addon(cfg, "@Alone", "Alone_Scripts", ["DZ_Data", "DZ_Scripts"])

    assert post_update.dependents(cfg, [framework]) == ["@Needs", "@NeedsThat"]
    assert post_update.offer(cfg, _failed_report(cfg, framework))["dependents"] == ["@Needs", "@NeedsThat"]


def test_the_load_order_still_skips_those_addon_names(cfg):
    from app.modmgr import mods as mods_mod

    _mods(cfg, {"@CF": "9000000001", "@Needs": "9000000002"})
    _addon(cfg, "@CF", "JM_CF_Scripts", ["DZ_Data"])
    _addon(cfg, "@Needs", "Needs_Scripts", ["JM_CF_Scripts"])
    assert mods_mod.get_mod_load_deps(cfg) == {}


def test_the_offer_names_the_newest_backup_and_whether_it_is_fresh(cfg, deps):
    from app.ops import backup_ops

    _mods(cfg, {"@CF": "1"})
    storage = cfg.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus" / "storage_1" / "data"
    storage.mkdir(parents=True)
    (storage / "vehicles.bin").write_bytes(b"x")
    assert post_update.offer(cfg, _failed_report(cfg, "@CF"))["backup"] is None
    started = time.time() - 5
    backup_ops.create_backup(cfg, backup_ops.BackupContents())
    plan = post_update.offer(cfg, _failed_report(cfg, "@CF", started=started))
    assert plan["backup"]["fresh"] is True
    plan = post_update.offer(cfg, _failed_report(cfg, "@CF", started=time.time() + 60))
    assert plan["backup"]["fresh"] is False


def test_a_disabled_mod_is_not_offered(cfg, deps):
    _mods(cfg, {"@CF": "1"})
    set_test_flags(cfg, disabled=["@CF"])
    assert post_update.offer(cfg, _failed_report(cfg, "@CF"))["mods"] == []


@pytest.fixture()
def aside(cfg, deps, monkeypatch):
    """set_aside with the server, the mission XML and the unit patched out."""
    from app.modmgr import mods as mods_mod
    from app.server import service_control

    seen: dict = {
        "restarts": 0, "watches": 0, "regenerated": 0, "xml": [], "order": [],
        "outcome": post_update.JOINABLE,
    }

    def _stop(config):
        seen["order"].append("stop")
        return service_control.ServiceResult(True, "")

    def _restart(config, anyway=False):
        assert anyway, "an automatic start must not stop at the runtime check"
        seen["restarts"] += 1
        seen["order"].append("start")
        return service_control.ServiceResult(True, "")

    async def _watch(config, since, **kw):
        seen["watches"] += 1
        return post_update.BootOutcome(seen["outcome"], 12.0, None, "detail")

    monkeypatch.setattr(service_control, "stop_server", _stop)
    monkeypatch.setattr(service_control, "start_server", _restart)
    monkeypatch.setattr(post_update, "watch_boot", _watch)
    monkeypatch.setattr(
        mods_mod, "apply_mod_state_to_mission_xml",
        lambda config, name, *, enable, log=None: seen["xml"].append((name, enable)),
    )

    async def _mutate(fn):
        fn(cfg)
        return cfg

    async def _regenerate(config):
        seen["regenerated"] += 1

    seen["run"] = lambda mods, backup_first=False, **kw: asyncio.run(post_update.set_aside(
        cfg, mods, mutate=_mutate, regenerate=_regenerate, backup_first=backup_first, **kw,
    ))
    return seen


def test_set_aside_disables_records_restarts_and_checks_once(cfg, deps, aside):
    _mods(cfg, {"@CF": "1", "@Trader": "2"})
    _workshop(cfg, {"1": 1_000, "2": 2_000})
    deps.update({"@Trader": {"@CF"}})
    _failed_report(cfg, "@CF")

    report = aside["run"](["@CF"])

    assert disabled_names(cfg) == ["@CF", "@Trader"]
    assert set(cfg.update_triage) == {"@CF", "@Trader"}
    assert cfg.update_triage["@CF"]["build"] == "200"
    assert cfg.update_triage["@CF"]["time_updated"] == 1_000
    assert aside["xml"] == [("@CF", False), ("@Trader", False)]
    assert (aside["regenerated"], aside["restarts"], aside["watches"]) == (1, 1, 1)
    assert aside["order"] == ["stop", "start"]
    assert report["set_aside"] == ["@CF", "@Trader"]
    assert report["recheck"]["outcome"] == post_update.JOINABLE
    assert post_update.load_report(cfg)["recheck"]["outcome"] == post_update.JOINABLE


def test_the_backup_first_holds_the_world_of_a_server_with_another_instance_id(cfg, deps, aside):
    """The boot without the mods deletes their objects, and this backup is the
    only way back. With instanceId = 2 the world is in storage_2."""
    import zipfile

    from app.modmgr import mods as mods_mod
    from app.ops import backup_ops

    _mods(cfg, {"@A": "1"})
    _failed_report(cfg, "@A")
    set_instance_id(cfg.server_dir_path, 2)
    mission = cfg.server_dir_path / "mpmissions" / mods_mod.get_current_mission(cfg.server_cfg_path)
    (mission / "storage_2" / "data").mkdir(parents=True)
    (mission / "storage_2" / "data" / "dynamic_000.bin").write_bytes(b"bases")

    aside["run"](["@A"], backup_first=True)

    (backup,) = backup_ops.list_backups(cfg)
    with zipfile.ZipFile(backup.zip_path) as zf:
        assert zf.read("data/dynamic_000.bin") == b"bases"
    assert disabled_names(cfg) == ["@A"]


def test_the_recheck_watches_the_new_boot_not_the_run_it_stopped(cfg, deps, aside, unit, monkeypatch):
    """A server that is still up writes its last lines while it stops, so its
    log is the oldest one written since the restart was asked for."""
    from app.server import service_control

    _mods(cfg, {"@A": "1"})
    _failed_report(cfg, "@A")
    old = _write_rpt(cfg, "old.RPT", loading_rpt(), age=60)

    def _stop(config):
        old.write_text(loading_rpt() + "19:50:00 --- Termination successfully completed ---\n", encoding="utf-8")
        time.sleep(0.05)
        return service_control.ServiceResult(True, "")

    def _start(config, anyway=False):
        assert anyway, "an automatic start must not stop at the runtime check"
        _write_rpt(cfg, "new.RPT", ready_rpt())
        return service_control.ServiceResult(True, "")

    async def _watch(config, since, **kw):
        return await _REAL_WATCH_BOOT(config, since, poll=0.01, timeout=1, **kw)

    monkeypatch.setattr(service_control, "stop_server", _stop)
    monkeypatch.setattr(service_control, "start_server", _start)
    monkeypatch.setattr(post_update, "watch_boot", _watch)

    report = aside["run"](["@A"])

    assert report["recheck"]["outcome"] == post_update.JOINABLE
    assert report["recheck"]["rpt"] == "new.RPT"


def test_a_stop_that_fails_starts_nothing_and_is_reported(cfg, deps, aside, monkeypatch):
    from app.server import service_control

    _mods(cfg, {"@A": "1"})
    _failed_report(cfg, "@A")
    monkeypatch.setattr(
        service_control, "stop_server", lambda config: service_control.ServiceResult(False, "no bus"),
    )

    report = aside["run"](["@A"])

    assert (aside["restarts"], aside["watches"]) == (0, 0)
    assert report["recheck"] == {"outcome": "not_started", "detail": "no bus"}


def test_a_failed_recheck_sets_nothing_more_aside(cfg, deps, aside, monkeypatch):
    _mods(cfg, {"@A": "1", "@B": "2"})
    _failed_report(cfg, "@A")
    aside["outcome"] = post_update.CRASHED
    monkeypatch.setattr(
        post_update, "suspects",
        lambda config, rpt, since: post_update.Attribution([post_update.Suspect("@B", ["x"])]),
    )

    report = aside["run"](["@A"])

    assert disabled_names(cfg) == ["@A"]
    assert aside["watches"] == 1
    assert report["recheck"]["suspects"][0]["mod"] == "@B"
    # The admin can still choose to act on the re-check's suspects.
    assert post_update.offerable(report) == ["@B"]


def test_set_aside_refuses_when_nothing_is_enabled(cfg, deps, aside):
    _mods(cfg, {"@A": "1"})
    set_test_flags(cfg, disabled=["@A"])
    _failed_report(cfg, "@A")
    with pytest.raises(ValueError):
        aside["run"](["@A"])
    assert aside["restarts"] == 0


def test_the_cli_offer_disables_nothing_without_a_yes(cfg, deps, aside, monkeypatch):
    from app.commands import mods as mods_cmd

    _mods(cfg, {"@A": "1"})
    report = _failed_report(cfg, "@A")
    monkeypatch.setattr(mods_cmd, "confirm", lambda *a, **k: k.get("default", False))

    assert mods_cmd.offer_set_aside(cfg, report, ni=True) is None
    assert disabled_names(cfg) == []
    assert aside["restarts"] == 0


def test_enabling_a_mod_by_hand_ends_its_triage(cfg):
    from app.modmgr.mods import mark_enabled

    set_test_flags(cfg, disabled=["@A"])
    cfg.update_triage = {"@A": {"build": "200", "time_updated": 1, "at": "x"}}
    assert mark_enabled(cfg, "@A")
    assert disabled_names(cfg) == [] and cfg.update_triage == {}


# --- bringing them back (plans/73 step 5) ---------------------------------------


def test_a_set_aside_mod_that_updated_since_is_noticed_once(cfg):
    _mods(cfg, {"@A": "1", "@B": "2"})
    _workshop(cfg, {"1": 1_000, "2": 2_000})
    set_test_flags(cfg, disabled=["@A", "@B"])
    cfg.update_triage = {
        "@A": {"build": "200", "time_updated": 1_000, "at": "x"},
        "@B": {"build": "200", "time_updated": 2_000, "at": "x"},
    }
    entries = {e["mod"]: e for e in post_update.triage_entries(cfg, remote={"1": 5_000})}
    assert entries["@A"]["updated_at"] == 5_000
    assert entries["@B"]["updated_at"] == 0

    fresh = post_update.newly_updated(cfg, remote={"1": 5_000})
    assert [e["mod"] for e in fresh] == ["@A"]
    assert post_update.triage_update_line(fresh[0]) == "@A has updated since DayZ build 200; enable it again?"
    post_update.mark_notified(cfg, fresh)
    assert post_update.newly_updated(cfg, remote={"1": 5_000}) == []
    # A later update is news again.
    assert [e["mod"] for e in post_update.newly_updated(cfg, remote={"1": 6_000})] == ["@A"]


def test_a_newer_installed_copy_counts_as_an_update(cfg):
    _mods(cfg, {"@A": "1"})
    _workshop(cfg, {"1": 3_000})
    cfg.update_triage = {"@A": {"build": "200", "time_updated": 1_000, "at": "x"}}
    assert post_update.triage_entries(cfg, remote={})[0]["updated_at"] == 3_000


def test_an_unknown_recorded_time_is_never_called_an_update(cfg):
    _mods(cfg, {"@A": "1"})
    cfg.update_triage = {"@A": {"build": "200", "time_updated": 0, "at": "x"}}
    assert post_update.triage_entries(cfg, remote={"1": 9_000})[0]["updated_at"] == 0


def test_a_logged_mod_that_is_not_one_folder_is_never_read(cfg, tmp_path):
    """The RPT's command line is read back from a file: an absolute or ".."
    entry must not send the PBO reader outside the server dir."""
    from app.modmgr import mod_conflict

    outside = tmp_path / "outside" / "@Evil"
    _pbo(outside / "addons" / "evil.pbo", "Evil")
    escape = "../outside/@Evil"
    assert mod_conflict.mod_pbo_prefixes(cfg.server_dir_path, [str(outside), escape, "@Missing"]) == {"@Missing": []}

    rpt = _write_rpt(cfg, "x.RPT", (
        f"== ./DayZServer -mod={outside};{escape}\n"
        '10:00:00 SCRIPT    (E): @"Evil/Scripts/4_World/a.c,1": Unknown type \'A\'\n'
    ))
    found = post_update.suspects(cfg, rpt, since=time.time() - 1)
    assert found.suspects == []
    assert len(found.unattributed) == 1


def _set_aside_mod(cfg):
    _mods(cfg, {"@A": "1"})
    _workshop(cfg, {"1": 1_000})
    set_test_flags(cfg, disabled=["@A"])
    cfg.update_triage = {"@A": {"build": "200", "time_updated": 1_000, "at": "x"}}


def test_check_updates_says_a_set_aside_mod_has_updated_once(cfg, monkeypatch):
    """plans/73 D: the scheduled mod_check runs this; it notifies, never enables."""
    from click.testing import CliRunner

    from app.commands import mods as mods_cmd
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck
    from app.monitoring import discord_hooks

    cfg.steam_api_key = "k"
    _set_aside_mod(cfg)
    sent = []
    monkeypatch.setattr(mods_cmd.DayzConfig, "load", staticmethod(lambda: cfg))
    monkeypatch.setattr(discord_hooks, "notify_triage_updated", lambda config, lines: sent.append(lines))
    check = UpdateCheck(updates=[ModUpdateInfo("1", "A", 5_000, 1_000)], checked=1, total=1, checked_at=1)
    monkeypatch.setattr("app.modmgr.mod_ops.detect_mod_updates", lambda config: check)

    result = CliRunner().invoke(mods_cmd.mods_group, ["check-updates"])
    assert result.exit_code == 0, result.output
    assert "@A has updated since DayZ build 200; enable it again?" in result.output
    assert sent == [["@A has updated since DayZ build 200; enable it again?"]]
    assert disabled_names(cfg) == ["@A"]
    assert cfg.update_triage["@A"]["notified"] == 5_000

    result = CliRunner().invoke(mods_cmd.mods_group, ["check-updates"])
    assert "has updated since" not in result.output
    assert len(sent) == 1


async def test_the_notifier_announces_a_set_aside_mod_once(cfg, monkeypatch):
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck
    from app.monitoring import discord_hooks
    from app.web.notifier import WebNotifier

    class Hub:
        messages: list = []

        async def broadcast(self, message, channel=None):
            self.messages.append(message)

    _set_aside_mod(cfg)
    sent = []
    monkeypatch.setattr(discord_hooks, "notify_triage_updated", lambda config, lines: sent.append(lines))
    saved = []

    async def _saver(fn):
        fn(cfg)
        saved.append(True)
        return cfg

    hub = Hub()
    wn = WebNotifier(cfg, hub, config_saver=_saver)
    check = UpdateCheck(updates=[ModUpdateInfo("1", "A", 5_000, 1_000)], checked=1, total=1, checked_at=1)

    await wn._announce_triage_updates(check)
    await wn._announce_triage_updates(check)

    toasts = [m for m in hub.messages if m["type"] == "notification"]
    assert [t["message"] for t in toasts] == ["@A has updated since DayZ build 200; enable it again?"]
    assert toasts[0]["link"] == "/mods"
    assert len(sent) == 1 and saved == [True]


def test_discord_escapes_set_aside_lines(cfg, monkeypatch):
    from app.monitoring import discord_hooks

    cfg.discord_webhook_url = "https://discord.example/hook"
    cfg.discord_hook_mod_updates = True
    sent = []
    monkeypatch.setattr(discord_hooks, "_send_embed", lambda url, embed: sent.append(embed))
    discord_hooks.notify_triage_updated(cfg, ["@Evil_Mod has updated since DayZ build 200; enable it again?"])
    assert "@Evil\\_Mod" in sent[0]["description"]


def test_the_mods_list_carries_the_set_aside_entry(cfg):
    from fastapi.testclient import TestClient

    from tests.test_web_update_triage import _app, _login

    _set_aside_mod(cfg)
    with TestClient(_app(cfg)) as client:
        _login(client)
        rows = {m["name"]: m for m in client.get("/api/mods").json()["mods"]}
    assert rows["@A"]["set_aside"]["build"] == "200"
    assert rows["@A"]["enabled"] is False
