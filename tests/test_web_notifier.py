"""Tests for app.web.notifier.WebNotifier, asserting on hub broadcasts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.rcon.rcon_scheduler import RestartWarningState
from app.web import notifier as notifier_mod
from app.web.notifier import WebNotifier
from tests.conftest import set_test_flags, write_test_mods


class RecordingHub:
    def __init__(self):
        self.messages: list[dict] = []

    async def broadcast(self, message, channel=None):
        self.messages.append(message)


async def _drain() -> None:
    """Let any asyncio.create_task-scheduled broadcasts run (on_player_event
    and the restart-warning toast both fire-and-forget a task rather than
    awaiting directly, since they're invoked from sync callback contexts)."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_on_player_event_broadcasts_notification(sample_config):
    sample_config.toast_player_connect = True
    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    wn.on_player_event("connect", "abcd1234", "Steve")
    await _drain()

    assert len(hub.messages) == 1
    assert hub.messages[0]["type"] == "notification"
    assert "Steve" in hub.messages[0]["message"]


async def test_on_player_event_disabled_by_config(sample_config):
    sample_config.toast_player_connect = False
    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    wn.on_player_event("connect", "abcd1234", "Steve")
    await _drain()

    assert hub.messages == []


async def test_restart_warning_skipped_when_server_not_running(sample_config, monkeypatch):
    sample_config.toast_restart_warning = True
    sample_config.restart_warning_minutes = [15, 10, 5, 3, 1]

    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    monkeypatch.setattr(notifier_mod, "_check_server_running", lambda config=None: False)
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45))
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: 60)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_restart_warning", lambda config, minutes: None)

    state = RestartWarningState(thresholds=sample_config.restart_warning_minutes)
    await wn._restart_warning_tick(state)
    await _drain()

    assert hub.messages == []


async def test_the_restart_warning_asks_about_this_instances_server(sample_config, monkeypatch):
    """A second instance's panel asked about main's unit (plans/62 step 0)."""
    import dataclasses

    from app.server import service_control

    pvp = dataclasses.replace(sample_config, instance_id="pvp", toast_restart_warning=True)
    asked = []
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control, "systemd_unit_state",
                        lambda config=None: asked.append(config) or ("inactive", "dead"))

    wn = WebNotifier(pvp, RecordingHub())
    await wn._restart_warning_tick(RestartWarningState(thresholds=[5]))

    assert [c.instance_id for c in asked] == ["pvp"]


async def test_restart_warning_fires_when_server_running(sample_config, monkeypatch):
    sample_config.toast_restart_warning = True
    sample_config.restart_warning_minutes = [15, 10, 5, 3, 1]

    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    monkeypatch.setattr(notifier_mod, "_check_server_running", lambda config=None: True)
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45))
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: 60)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_restart_warning", lambda config, minutes: None)

    state = RestartWarningState(thresholds=sample_config.restart_warning_minutes)
    await wn._restart_warning_tick(state)
    await _drain()

    messages = [m["message"] for m in hub.messages if m["type"] == "notification"]
    assert messages == ["Server restart in ~15 min"]


async def test_crash_detection_seeds_without_alerting_on_first_run(sample_config):
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    (profiles / "crash1.dmp").write_text("x")

    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    found = wn._check_for_crash_dumps()
    assert found == []

    (profiles / "crash2.dmp").write_text("x")
    found = wn._check_for_crash_dumps()
    assert [f.name for f in found] == ["crash2.dmp"]


async def test_collect_and_record_metrics(sample_config, monkeypatch):
    db_path = sample_config.paths.metrics_db
    monkeypatch.setattr("app.server.server_status.read_server_fps", lambda profiles: "45.2")
    monkeypatch.setattr("app.server.server_status.read_server_memory", lambda profiles, config=None: "1.2 GB")
    monkeypatch.setattr("app.server.server_status.read_server_cpu", lambda config=None: "12%")

    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)
    wn._collect_and_record_metrics()

    from app.monitoring.metrics_db import query_recent

    snapshots = query_recent(hours=1, db_path=db_path)
    assert len(snapshots) == 1
    assert snapshots[0].fps == 45.2
    assert snapshots[0].memory_mb == 1228.8


async def test_start_schedules_eight_loops_and_stop_cancels_them(sample_config):
    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    wn.start()
    assert len(wn._tasks) == 8
    assert all(not t.done() for t in wn._tasks)

    await wn.stop()
    assert wn._tasks == []


async def test_notify_includes_link_only_when_set(sample_config):
    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)

    await wn._notify("with link", "warning", 8, link="/doctor")
    assert hub.messages[-1]["link"] == "/doctor"

    await wn._notify("no link")
    assert "link" not in hub.messages[-1]


def _doctor_notifier(sample_config, hub, monkeypatch, failures=None):
    sample_config.toast_doctor_check = True
    wn = WebNotifier(sample_config, hub)
    monkeypatch.setattr(
        wn,
        "_run_doctor_checks",
        lambda: failures
        if failures is not None
        else [("Server port", False, "Port 2302 in use by another (non-DayZ) process")],
    )
    return wn


async def test_doctor_loop_broadcasts_detail_and_deeplink(sample_config, monkeypatch):
    hub = RecordingHub()
    wn = _doctor_notifier(sample_config, hub, monkeypatch)

    await wn._doctor_check_tick()

    frames = [m for m in hub.messages if m["type"] == "notification"]
    assert frames
    assert "Port 2302" in frames[0]["message"]
    assert frames[0]["link"] == "/doctor"


# -- job-aware pausing ------------------------------------------------------
# A mutating job deletes and re-adds server/workshop files; checks run mid-job
# report failures the admin is in the middle of fixing.


class FakeJobs:
    """The slice of JobManager WebNotifier.attach_jobs consumes."""

    def __init__(self):
        self.busy = False
        self.on_finished = []

    def finish(self, job=None):
        self.busy = False
        for cb in self.on_finished:
            cb(job)


async def test_doctor_tick_skipped_while_job_running(sample_config, monkeypatch):
    hub = RecordingHub()
    wn = _doctor_notifier(sample_config, hub, monkeypatch)
    jobs = FakeJobs()
    wn.attach_jobs(jobs)

    jobs.busy = True
    assert wn.busy is True
    await wn._doctor_check_tick()
    assert [m for m in hub.messages if m["type"] == "notification"] == []

    # The skipped tick must not have consumed the baseline either: the same
    # failure still reads as new once the job is done.
    jobs.busy = False
    await wn._doctor_check_tick()
    assert [m for m in hub.messages if m["type"] == "notification"]


async def test_finished_job_wakes_doctor_loop_early(sample_config, monkeypatch):
    hub = RecordingHub()
    wn = _doctor_notifier(sample_config, hub, monkeypatch)
    jobs = FakeJobs()
    wn.attach_jobs(jobs)

    jobs.busy = True
    assert not wn._doctor_recheck.is_set()
    jobs.finish()

    # Woken by the job, not by the interval — _wait_doctor_tick returns True
    # without waiting out _DOCTOR_INTERVAL.
    woken = await asyncio.wait_for(wn._wait_doctor_tick(), timeout=1)
    assert woken is True
    assert not wn._doctor_recheck.is_set()  # cleared, so the next wait blocks


async def test_wait_doctor_tick_returns_false_on_interval(sample_config, monkeypatch):
    hub = RecordingHub()
    wn = _doctor_notifier(sample_config, hub, monkeypatch)
    monkeypatch.setattr(WebNotifier, "_DOCTOR_INTERVAL", 0.01)

    assert await wn._wait_doctor_tick() is False


async def test_stop_detaches_job_callback(sample_config, monkeypatch):
    hub = RecordingHub()
    wn = _doctor_notifier(sample_config, hub, monkeypatch)
    jobs = FakeJobs()
    wn.attach_jobs(jobs)
    assert jobs.on_finished

    await wn.stop()

    assert jobs.on_finished == []
    jobs.busy = True
    assert wn.busy is False  # no longer reading the detached manager


async def test_restart_warning_counts_down_to_the_scheduled_restart(sample_config, monkeypatch, timers_unknown):
    """TODO C3b: a `restart` task fires the warnings, not just messages.xml.

    The countdown used to read only the engine's own `<shutdown>/<deadline>`,
    so a cron restart at 06:00 warned nobody.
    """
    import app.server.scheduler as sched

    sample_config.toast_restart_warning = True
    sample_config.restart_warning_minutes = [15, 10, 5, 3, 1]
    sample_config.scheduled_tasks = [
        {"id": "nightly", "task_type": "restart", "cron": "0 6 * * *", "enabled": True}
    ]

    plus_two = timezone(timedelta(hours=2))
    # 05:48 — twelve minutes out, so the 15-minute threshold is due.
    monkeypatch.setattr(sched, "cron_now", lambda: datetime(2026, 9, 21, 5, 48, tzinfo=plus_two))
    monkeypatch.setattr(notifier_mod, "_check_server_running", lambda config=None: True)
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45))
    # No engine deadline at all — the old code had nothing to count down from.
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: None)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_restart_warning", lambda config, minutes: None)

    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)
    state = RestartWarningState(thresholds=sample_config.restart_warning_minutes)
    await wn._restart_warning_tick(state)
    await _drain()

    # The 15-minute threshold, reached 12 minutes out, says what is left.
    assert [m["message"] for m in hub.messages] == ["Server restart in ~12 min"]


async def test_the_toast_switch_does_not_silence_the_in_game_warning(sample_config, monkeypatch, timers_unknown):
    """toast_restart_warning used to return before the whole cycle, so turning
    off the panel's toast also stopped the RCon warnings and the join lock."""
    from unittest.mock import AsyncMock, MagicMock

    import app.server.scheduler as sched

    sample_config.toast_restart_warning = False
    sample_config.restart_warning_minutes = [15]
    sample_config.scheduled_tasks = [
        {"id": "nightly", "task_type": "restart", "cron": "0 6 * * *", "enabled": True}
    ]
    plus_two = timezone(timedelta(hours=2))
    monkeypatch.setattr(sched, "cron_now", lambda: datetime(2026, 9, 21, 5, 48, tzinfo=plus_two))
    monkeypatch.setattr(notifier_mod, "_check_server_running", lambda config=None: True)
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45))
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: None)
    monkeypatch.setattr("app.monitoring.discord_hooks.notify_restart_warning", lambda config, minutes: None)

    rcon = MagicMock()
    rcon.is_connected = MagicMock(return_value=True)
    rcon.send_message = AsyncMock()
    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)
    wn.set_rcon_manager(rcon)
    await wn._restart_warning_tick(RestartWarningState(thresholds=[15]))
    await _drain()

    assert rcon.send_message.await_count == 1
    assert hub.messages == []


async def test_scheduled_restart_wins_over_the_engine_deadline(sample_config, monkeypatch, timers_unknown):
    import app.server.scheduler as sched

    sample_config.scheduled_tasks = [
        {"id": "nightly", "task_type": "restart", "cron": "0 6 * * *", "enabled": True}
    ]
    plus_two = timezone(timedelta(hours=2))
    monkeypatch.setattr(sched, "cron_now", lambda: datetime(2026, 9, 21, 5, 48, tzinfo=plus_two))
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45))
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: 60)

    wn = WebNotifier(sample_config, RecordingHub())
    assert wn._get_remaining_and_deadline() == (12, 1440)


async def test_engine_deadline_still_used_without_a_restart_task(sample_config, monkeypatch):
    """A server that really does restart itself on messages.xml keeps working."""
    sample_config.scheduled_tasks = []
    monkeypatch.setattr("app.server.server_status.get_server_uptime", lambda config=None: timedelta(minutes=45))
    monkeypatch.setattr("app.server.server_status.get_shutdown_deadline", lambda config: 60)

    wn = WebNotifier(sample_config, RecordingHub())
    assert wn._get_remaining_and_deadline() == (15, 60)


def test_log_loop_failure_warns_once_then_debugs(sample_config, caplog):
    wn = WebNotifier(sample_config, RecordingHub())
    caplog.set_level(logging.DEBUG, logger="app.web.notifier")
    wn._log_loop_failure("k", "m")
    wn._log_loop_failure("k", "m")

    records = [r for r in caplog.records if r.name == "app.web.notifier"]
    assert [r.levelno for r in records] == [logging.WARNING, logging.DEBUG]


def test_log_loop_failure_keys_are_independent(sample_config, caplog):
    wn = WebNotifier(sample_config, RecordingHub())
    caplog.set_level(logging.DEBUG, logger="app.web.notifier")
    wn._log_loop_failure("a", "first loop broke")
    wn._log_loop_failure("b", "second loop broke")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


async def test_metrics_loop_never_sleeps_less_than_the_floor(sample_config, monkeypatch):
    """plans/51 A (N7): `asyncio.sleep(0)` returns at once, so a
    `metrics_interval` of 0 in a hand-edited config.json (which never passes
    through settings_registry) made the loop collect back to back."""
    from app.shared.settings_registry import METRICS_INTERVAL_MIN

    sample_config.metrics_interval = 0
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(notifier_mod.asyncio, "sleep", fake_sleep)
    wn = WebNotifier(sample_config, RecordingHub())
    try:
        await wn._metrics_collection_loop()
    except asyncio.CancelledError:
        pass
    assert slept == [METRICS_INTERVAL_MIN]


async def _one_metrics_tick(sample_config, monkeypatch, collect):
    """Run the metrics loop for exactly one collection and return the hub."""
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(notifier_mod.asyncio, "sleep", fake_sleep)
    sample_config.metrics_enabled = True
    hub = RecordingHub()
    wn = WebNotifier(sample_config, hub)
    monkeypatch.setattr(wn, "_collect_and_record_metrics", collect)
    try:
        await wn._metrics_collection_loop()
    except asyncio.CancelledError:
        pass
    return hub


async def test_metrics_tick_broadcasts_metrics_recorded(sample_config, monkeypatch):
    """The Dashboard's Performance tile reloads on this message, since a tile
    never runs its own timer."""
    hub = await _one_metrics_tick(sample_config, monkeypatch, lambda: None)
    assert {"type": "metrics_recorded"} in hub.messages


async def test_failed_metrics_tick_broadcasts_nothing(sample_config, monkeypatch):
    def boom():
        raise OSError("disk")

    hub = await _one_metrics_tick(sample_config, monkeypatch, boom)
    assert {"type": "metrics_recorded"} not in hub.messages


async def _one_update_tick(sample_config, monkeypatch, check):
    """Run the mod-update loop for exactly one check."""
    sample_config.mod_update_check_interval = 3600
    sample_config.steam_api_key = "k"
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(notifier_mod.asyncio, "sleep", fake_sleep)
    wn = WebNotifier(sample_config, RecordingHub())
    monkeypatch.setattr(wn, "_check_mod_updates", check)
    try:
        await wn._mod_update_check_loop()
    except asyncio.CancelledError:
        pass


async def test_mod_update_loop_saves_an_empty_answer(sample_config, monkeypatch):
    """plans/50 B: a check that finds nothing clears a stale list; before, only
    a non-empty answer was saved, so an updated mod stayed flagged."""
    from app.modmgr import workshop_api
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck
    from app.shared.paths import CONFIG_DIR

    path = CONFIG_DIR / "mod_updates.json"
    workshop_api.save_detected_updates(
        path, UpdateCheck(updates=[ModUpdateInfo("1", "A", 10, 5)], checked=1, total=1, checked_at=1),
    )
    await _one_update_tick(sample_config, monkeypatch, lambda: UpdateCheck(checked=1, total=1, checked_at=2))
    check = workshop_api.load_update_check(path)
    assert check.updates == [] and check.checked_at == 2


async def test_mod_update_loop_keeps_the_last_answer_when_a_check_fails(sample_config, monkeypatch):
    from app.modmgr import workshop_api
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck
    from app.shared.paths import CONFIG_DIR

    path = CONFIG_DIR / "mod_updates.json"
    workshop_api.save_detected_updates(
        path, UpdateCheck(updates=[ModUpdateInfo("1", "A", 10, 5)], checked=1, total=1, checked_at=1),
    )
    before = path.read_bytes()

    def _fail():
        raise workshop_api.UpdateCheckError(workshop_api.UNREACHABLE, "down")

    await _one_update_tick(sample_config, monkeypatch, _fail)
    assert path.read_bytes() == before


def test_the_notifier_snapshots_a_start_it_did_not_see(sample_config, monkeypatch):
    """plans/56 D: a scheduled or crash restart runs no zsm code, so zsm
    web takes its start snapshot. The decision is changeset_ops'; this pins
    that the notifier asks with the unit's own start time."""
    from app.ops import changeset_ops
    from app.server import server_status

    started = datetime(2026, 9, 26, 6, 0, 0)
    calls = []
    monkeypatch.setattr(server_status, "get_server_start_time", lambda config=None: started)
    monkeypatch.setattr(
        changeset_ops, "snapshot_unrecorded_start",
        lambda config, when: calls.append((config, when)) or True,
    )
    wn = WebNotifier(sample_config, RecordingHub())

    assert wn._snapshot_unrecorded_start() is True
    assert calls == [(sample_config, started)]


def test_the_discord_status_counts_the_mods_the_server_loads(sample_config, monkeypatch):
    # mods.txt is written as `@Name;` lines; the count read the old `@Name=ID`
    # form only, so it was always 0 and the embed had no Mods field.
    import app.commands.service as service

    monkeypatch.setattr(service, "is_server_running", lambda config=None: True)
    write_test_mods(sample_config, ['@CF', '@Dabs Framework', '@VPPAdminTools'])
    set_test_flags(sample_config, disabled=["@VPPAdminTools"])

    embed = WebNotifier(sample_config, RecordingHub())._build_discord_status_embed()

    assert {"name": "Mods", "value": "2", "inline": True} in embed["fields"]
