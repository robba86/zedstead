"""Tests for cli.server.scheduler and cli.rcon_scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.server.scheduler import (
    SCHEDULE_PRESETS,
    TASK_COMMANDS,
    ScheduledTask,
    add_task,
    cron_to_oncalendar,
    cron_to_schtasks_triggers,
    generate_schtasks_files,
    generate_schtasks_xml,
    generate_service_content,
    generate_timer_content,
    generate_timer_files,
    install_timers,
    load_tasks,
    remove_task,
    remove_timers,
    save_tasks,
    update_task,
    validate_cron,
    validate_task,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_config(tmp_path: Path, monkeypatch):
    from app.shared.config import DayzConfig

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"

    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("app.shared.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_file)

    cfg = DayzConfig(
        server_dir=str(tmp_path / "server"),
        steamcmd_dir=str(tmp_path / "steamcmd"),
        steam_username="testuser",
    )
    cfg.save()
    return cfg


# ---------------------------------------------------------------------------
# ScheduledTask
# ---------------------------------------------------------------------------


class TestScheduledTask:
    def test_from_dict(self):
        task = ScheduledTask.from_dict({
            "id": "test",
            "task_type": "restart",
            "cron": "0 */4 * * *",
            "enabled": True,
        })
        assert task.id == "test"
        assert task.task_type == "restart"

    def test_to_dict_roundtrip(self):
        task = ScheduledTask(id="t1", task_type="backup", cron="0 6 * * *")
        data = task.to_dict()
        restored = ScheduledTask.from_dict(data)
        assert restored.id == task.id
        assert restored.cron == task.cron

    def test_label(self):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *")
        assert task.label == "Server Restart"

    def test_description_human_readable(self):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *")
        desc = task.description
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_next_run(self):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 */4 * * *")
        nxt = task.next_run
        assert nxt is not None

    def test_next_run_display(self):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 */4 * * *")
        display = task.next_run_display
        assert isinstance(display, str)

    def test_unknown_extra_fields_ignored(self):
        task = ScheduledTask.from_dict({
            "id": "t1", "task_type": "restart", "cron": "0 4 * * *",
            "unknown_field": "ignored",
        })
        assert task.id == "t1"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_cron(self):
        assert validate_cron("0 */4 * * *") is True
        assert validate_cron("0 4 * * *") is True
        assert validate_cron("*/5 * * * *") is True
        assert validate_cron("0 0 * * 1") is True

    def test_invalid_cron(self):
        assert validate_cron("not a cron") is False
        assert validate_cron("") is False
        assert validate_cron("60 25 32 13 8") is False

    def test_a_seconds_field_is_refused(self):
        # croniter accepts six fields, but the timer converter reads five:
        # this became an hourly timer at a random minute (test pass 2, N18).
        assert validate_cron("* * * * * *") is False
        assert validate_cron("0 0 4 * * *") is False

    @pytest.mark.parametrize("cron", ["0 0 31 2 *", "0 0 30 feb *", "0 0 31 4,6 *", "0 0 31 2,4 *"])
    def test_a_date_that_never_comes_is_refused(self, cron):
        # croniter calls these valid and then finds no next run.
        from app.server.scheduler import cron_problem

        assert "never" in cron_problem(cron)

    @pytest.mark.parametrize("cron", ["0 0 29 2 *", "0 0 31 2,3 *", "0 0 L 2 *", "0 0 */15 2 *"])
    def test_a_rare_date_that_does_come_is_kept(self, cron):
        assert validate_cron(cron) is True

    def test_every_schedule_preset_is_valid(self):
        from app.server.scheduler import SCHEDULE_PRESETS

        for key, preset in SCHEDULE_PRESETS.items():
            assert validate_cron(preset["cron"]), key

    def test_validate_task_ok(self):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *")
        assert validate_task(task) == []

    def test_validate_task_errors(self):
        task = ScheduledTask(id="", task_type="invalid", cron="bad")
        errors = validate_task(task)
        assert len(errors) == 3  # id, type, cron

    @pytest.mark.parametrize("bad_id", ["nightly\n", "nightly\r\n", "\nnightly", "night ly"])
    def test_an_id_with_a_line_break_or_space_is_refused(self, bad_id):
        # The id is a unit file name and part of ExecStart= (plans/68 INJ-5).
        task = ScheduledTask(id=bad_id, task_type="restart", cron="0 4 * * *")
        assert validate_task(task) == [
            "Task ID must contain only letters, digits, hyphens, and underscores"
        ]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_add_and_load(self, mock_config):
        task = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")
        add_task(mock_config, task)

        loaded = load_tasks(mock_config)
        assert len(loaded) == 1
        assert loaded[0].id == "restart_4h"

    def test_add_duplicate_raises(self, mock_config):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *")
        add_task(mock_config, task)
        with pytest.raises(ValueError, match="already exists"):
            add_task(mock_config, task)

    def test_add_invalid_raises(self, mock_config):
        task = ScheduledTask(id="", task_type="restart", cron="bad")
        with pytest.raises(ValueError):
            add_task(mock_config, task)

    def test_update(self, mock_config):
        task = ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *")
        add_task(mock_config, task)
        updated = update_task(mock_config, "t1", enabled=False, cron="0 6 * * *")
        assert updated is not None
        assert updated.enabled is False
        assert updated.cron == "0 6 * * *"

    def test_update_nonexistent(self, mock_config):
        assert update_task(mock_config, "missing", enabled=False) is None

    def test_remove(self, mock_config):
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        assert remove_task(mock_config, "t1") is True
        assert load_tasks(mock_config) == []

    def test_remove_nonexistent(self, mock_config):
        assert remove_task(mock_config, "missing") is False

    def test_save_multiple(self, mock_config):
        tasks = [
            ScheduledTask(id="t1", task_type="restart", cron="0 */4 * * *"),
            ScheduledTask(id="t2", task_type="backup", cron="0 */6 * * *"),
        ]
        save_tasks(mock_config, tasks)
        loaded = load_tasks(mock_config)
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# Cron to OnCalendar
# ---------------------------------------------------------------------------


class TestCronToOnCalendar:
    def test_every_4_hours(self):
        result = cron_to_oncalendar("0 */4 * * *")
        assert "00/4" in result
        assert "00:00" in result

    def test_daily_at_4am(self):
        result = cron_to_oncalendar("0 4 * * *")
        assert "04" in result

    def test_every_5_minutes(self):
        result = cron_to_oncalendar("*/5 * * * *")
        assert "00/5" in result

    def test_monday_at_4am(self):
        result = cron_to_oncalendar("0 4 * * 1")
        assert "Mon" in result

    def test_weekday_range(self):
        result = cron_to_oncalendar("0 4 * * 1-5")
        assert "Mon..Fri" in result

    def test_weekday_list(self):
        result = cron_to_oncalendar("0 4 * * 0,6")
        assert "Sun" in result
        assert "Sat" in result

    def test_all_stars(self):
        result = cron_to_oncalendar("* * * * *")
        assert "*" in result

    # Range/list translation: cron uses '-' for ranges, systemd uses '..'.
    # Leaving '-' in place produces an invalid OnCalendar spec; in the month
    # field the '-' even collides with the Year-Month-Day separator.
    # Expected strings below were validated with `systemd-analyze calendar`.

    def test_hour_range_uses_double_dot(self):
        assert cron_to_oncalendar("0 9-17 * * *") == "*-*-* 09..17:00:00"

    def test_day_of_month_range_uses_double_dot(self):
        assert cron_to_oncalendar("0 0 1-15 * *") == "*-*-1..15 00:00:00"

    def test_month_range_does_not_collide_with_separator(self):
        # Regression: month "1-6" used to render as "*-1-6-*" (four segments).
        assert cron_to_oncalendar("0 0 * 1-6 *") == "*-1..6-* 00:00:00"

    def test_day_of_month_list(self):
        assert cron_to_oncalendar("0 0 1,15 * *") == "*-*-1,15 00:00:00"

    def test_minute_list(self):
        assert cron_to_oncalendar("15,45 * * * *") == "*-*-* *:15,45:00"

    def test_day_step_is_anchored_not_star_step(self):
        # systemd rejects a '*' step base ("*/2"); it must anchor at day 1.
        assert cron_to_oncalendar("0 0 */2 * *") == "*-*-1/2 00:00:00"

    def test_month_step_is_anchored(self):
        assert cron_to_oncalendar("0 0 * */3 *") == "*-1/3-* 00:00:00"

    def test_combined_ranges_and_steps(self):
        assert (
            cron_to_oncalendar("*/15 9-17 * * 1-5")
            == "Mon..Fri *-*-* 09..17:00/15:00"
        )

    # croniter accepts these, and each used to become a spec systemd refuses
    # (plans/68 B7). Every expected spec was checked with `systemd-analyze
    # calendar --iterations=12` against croniter's next twelve firings.
    @pytest.mark.parametrize("cron, spec", [
        ("0 4 L * *", "*-*~01 04:00:00"),
        ("0 4 L feb *", "*-2~01 04:00:00"),
        ("0 4 * * 5#3", "Fri *-*-15..21 04:00:00"),
        ("0 4 * * fri#5", "Fri *-*-29..31 04:00:00"),
        ("0 4 * jan-mar 5#3", "Fri *-1..3-15..21 04:00:00"),
        ("0 4 * * L5", "Fri *-*~07/1 04:00:00"),
        ("0 4 * jan *", "*-1-* 04:00:00"),
        ("0 4 * JAN,FEB *", "*-1,2-* 04:00:00"),
        ("0 4 * jan/2 *", "*-1/2-* 04:00:00"),
        ("0 4 ? * *", "*-*-* 04:00:00"),
        ("0 4 * * ?", "*-*-* 04:00:00"),
        ("0 4 * * */2", "Sun,Tue,Thu,Sat *-*-* 04:00:00"),
        ("0 4 * * 1-5/2", "Mon,Wed,Fri *-*-* 04:00:00"),
        ("0 4 * * 2/2", "Tue,Thu,Sat *-*-* 04:00:00"),
    ])
    def test_croniter_forms_systemd_has_no_word_for_are_translated(self, cron, spec):
        assert validate_cron(cron)
        assert cron_to_oncalendar(cron) == spec

    def test_an_nth_weekday_name_is_not_every_such_weekday(self):
        # "sun#1" used to become plain "Sun": every Sunday, not the first.
        assert cron_to_oncalendar("0 4 * * sun#1") == "Sun *-*-01..07 04:00:00"

    @pytest.mark.parametrize("cron, reason", [
        ("0 4 15W * *", "no nearest-weekday 'W'"),
        ("0 4 L,15 * *", "last day 'L' only on its own"),
        ("0 4 1-L * *", "last day 'L' only on its own"),
        ("0 4 1 * 5#3", "cannot combine an nth or last weekday with a day of the month"),
        ("0 4 * * L5,L1", "nth or last weekday only on its own"),
    ])
    def test_forms_systemd_cannot_express_are_refused_with_the_reason(self, cron, reason):
        assert validate_cron(cron) is False
        errors = validate_task(ScheduledTask(id="t1", task_type="restart", cron=cron))
        assert len(errors) == 1
        assert errors[0].startswith(f"Invalid cron expression: {cron} (systemd timers")
        assert reason in errors[0]

    # plans/68 B20: cron's week starts on Sunday (0 or 7) and systemd's on
    # Monday, where a range may not run backwards. Each spec was checked with
    # `systemd-analyze calendar --iterations=40` against croniter's firings.
    @pytest.mark.parametrize("cron, spec", [
        ("0 4 * * 0-7", "Mon..Sun *-*-* 04:00:00"),  # was Sun..Sun: Sundays only
        ("0 4 * * 0-6", "Mon..Sun *-*-* 04:00:00"),  # was Sun..Sat: refused
        ("0 4 * * sun-sat", "Mon..Sun *-*-* 04:00:00"),
        ("0 4 * * 0-3", "Mon..Wed,Sun *-*-* 04:00:00"),
        ("0 4 * * 0-1", "Mon,Sun *-*-* 04:00:00"),
        ("0 4 * * 0-2,5", "Mon..Tue,Sun,Fri *-*-* 04:00:00"),
        ("0 4 * * 5-1", "Mon,Fri..Sun *-*-* 04:00:00"),  # wraps, as croniter reads it
        ("0 4 * * 7-1", "Mon,Sun *-*-* 04:00:00"),
        ("0 4 * * 7/2", "Sun,Tue,Thu,Sat *-*-* 04:00:00"),  # was no weekday: every day
    ])
    def test_a_weekday_range_through_sunday_means_what_cron_means(self, cron, spec):
        assert validate_cron(cron)
        assert cron_to_oncalendar(cron) == spec

    # A range systemd already read right keeps its spelling, so its installed
    # timer does not read as outdated.
    @pytest.mark.parametrize("cron, spec", [
        ("0 4 * * 1-7", "Mon..Sun *-*-* 04:00:00"),
        ("0 4 * * 5-7", "Fri..Sun *-*-* 04:00:00"),
        ("0 4 * * 7", "Sun *-*-* 04:00:00"),
        ("0 4 * * 6-0", "Sat..Sun *-*-* 04:00:00"),
        ("0 4 * * 3-0", "Wed..Sun *-*-* 04:00:00"),
        ("0 4 * * mon-fri", "Mon..Fri *-*-* 04:00:00"),
        ("0 4 * * 0-7/2", "Sun,Tue,Thu,Sat *-*-* 04:00:00"),
        ("0 4 * * 1-7/2", "Mon,Wed,Fri,Sun *-*-* 04:00:00"),
        ("0 4 * * 5-7/2", "Fri,Sun *-*-* 04:00:00"),
    ])
    def test_a_weekday_range_systemd_read_right_is_unchanged(self, cron, spec):
        assert cron_to_oncalendar(cron) == spec

    # plans/68 B21: cron (and croniter, behind "Next run") fires when the day
    # OR the weekday matches; OnCalendar= can only say both.
    DAY_OR_WEEKDAY = (
        "cron runs this when either the day or the weekday matches, and systemd "
        "timers only when both do; split it into two tasks, or write the first "
        "Monday of a month as 1#1"
    )

    @pytest.mark.parametrize("cron", [
        "0 4 1 * 1",
        "0 4 1-7 * 1",  # the "first Monday" attempt: days 1-7 and every Monday
        "0 4 */2 * 1",  # croniter counts a star step as restricted
        "0 4 L * 5",
        "0 4 1,15 * mon-fri",
        "0 4 1-30 * 0-7",  # every weekday, but not every day: the 31st differs
    ])
    def test_a_day_and_a_weekday_together_are_refused_with_the_reason(self, cron):
        assert validate_cron(cron) is False
        assert validate_task(ScheduledTask(id="t1", task_type="restart", cron=cron)) == [
            f"Invalid cron expression: {cron} ({self.DAY_OR_WEEKDAY})"
        ]

    @pytest.mark.parametrize("cron, spec", [
        ("0 4 1 * *", "*-*-1 04:00:00"),
        ("0 4 * * 1", "Mon *-*-* 04:00:00"),
        ("0 4 ? * 1", "Mon *-*-* 04:00:00"),
        ("0 4 1 * ?", "*-*-1 04:00:00"),
        ("0 4 * * 1#1", "Mon *-*-01..07 04:00:00"),
        # Both cover every day, so either reading is every day.
        ("0 4 1-31 * 1-7", "Mon..Sun *-*-1..31 04:00:00"),
        ("0 4 1-30 4,6,9,11 0-7", "Mon..Sun *-4,6,9,11-1..30 04:00:00"),
    ])
    def test_one_of_the_two_restricted_is_unaffected(self, cron, spec):
        assert validate_cron(cron)
        assert cron_to_oncalendar(cron) == spec

    def test_a_stored_task_with_both_is_skipped_at_install_with_the_reason(self, mock_config):
        mock_config.scheduled_tasks = [
            ScheduledTask(id="first_monday", task_type="restart", cron="0 4 1-7 * 1").to_dict(),
            ScheduledTask(id="nightly", task_type="backup", cron="0 4 * * *").to_dict(),
        ]
        lines: list[str] = []

        assert [f[0] for f in generate_timer_files(mock_config, log=lines.append)] == [
            "dayz-task-nightly"
        ]
        assert lines == [
            "Skipped scheduled task 'first_monday': Invalid cron expression: "
            f"0 4 1-7 * 1 ({self.DAY_OR_WEEKDAY})"
        ]

    def test_a_stepped_weekday_range_past_sunday_is_refused(self):
        # croniter reads 6-1/2 as Saturday alone, cron arithmetic as Saturday
        # and Monday; the timer used to run every day.
        errors = validate_task(ScheduledTask(id="t1", task_type="restart", cron="0 4 * * 6-1/2"))
        assert errors == [
            "Invalid cron expression: 0 4 * * 6-1/2 (systemd timers cannot step a "
            "weekday range that runs past Sunday; list the days instead)"
        ]


# ---------------------------------------------------------------------------
# Timer generation
# ---------------------------------------------------------------------------


class TestTimerGeneration:
    def test_generate_timer_content(self):
        task = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")
        content = generate_timer_content(task)
        assert "[Timer]" in content
        assert "OnCalendar=" in content
        assert "Persistent=true" in content
        assert "restart_4h" in content

    def test_generate_timer_files(self, mock_config):
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        add_task(mock_config, ScheduledTask(id="t2", task_type="backup", cron="0 6 * * *", enabled=False))

        files = generate_timer_files(mock_config)
        assert len(files) == 1  # only enabled tasks
        assert files[0][0] == "dayz-task-t1"

    def test_a_hand_edited_task_that_does_not_validate_is_skipped_and_named(self, mock_config):
        # Only add/update validate; config.json can be edited by hand (plans/68 INJ-9).
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        mock_config.scheduled_tasks += [
            {"id": "evil\nExecStartPre=/bin/true", "task_type": "restart", "cron": "0 4 * * *"},
            {"id": "t3", "task_type": "restart\nX=1", "cron": "0 4 * * *"},
        ]
        lines: list[str] = []

        files = generate_timer_files(mock_config, log=lines.append)

        assert [f[0] for f in files] == ["dayz-task-t1"]
        assert not any("X=1" in t or "X=1" in s for _, t, s in files)
        assert len(lines) == 2
        assert lines[0].startswith("Skipped scheduled task 'evil\\nExecStartPre=/bin/true': Task ID")
        assert lines[1] == "Skipped scheduled task 't3': Unknown task type: restart\nX=1"

        lines.clear()
        assert [f[0] for f in generate_schtasks_files(mock_config, log=lines.append)] == [
            "dayz-task-t1"
        ]
        assert len(lines) == 2

    def test_install_timers_to_dir(self, mock_config, tmp_path):
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        dest = tmp_path / "systemd"
        dest.mkdir()

        installed = install_timers(mock_config, dest_dir=dest)
        assert len(installed) == 1
        assert (dest / "dayz-task-t1.timer").is_file()
        assert (dest / "dayz-task-t1.service").is_file()

    def test_install_timers_user_scope_writes_to_xdg_no_sudo(
        self, mock_config, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        mock_config.service_scope = "user"
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        cmds = []
        monkeypatch.setattr(
            "app.server.scheduler.subprocess.run",
            lambda cmd, **k: cmds.append(cmd),
        )
        installed = install_timers(mock_config)  # no dest_dir → real install path
        assert installed == ["dayz-task-t1"]
        unit_dir = tmp_path / "xdg" / "systemd" / "user"
        assert (unit_dir / "dayz-task-t1.timer").is_file()
        assert (unit_dir / "dayz-task-t1.service").is_file()
        assert cmds[0] == ["systemctl", "--user", "daemon-reload"]
        assert ["systemctl", "--user", "enable", "dayz-task-t1.timer"] in cmds
        assert ["systemctl", "--user", "start", "dayz-task-t1.timer"] in cmds
        assert all("sudo" not in c for c in cmds)

    def test_remove_timers_user_scope_unlinks_from_xdg_no_sudo(
        self, mock_config, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        mock_config.service_scope = "user"
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        monkeypatch.setattr("app.server.scheduler.subprocess.run", lambda cmd, **k: None)
        install_timers(mock_config)
        unit_dir = tmp_path / "xdg" / "systemd" / "user"
        assert (unit_dir / "dayz-task-t1.timer").is_file()

        cmds = []
        monkeypatch.setattr(
            "app.server.scheduler.subprocess.run",
            lambda cmd, **k: cmds.append(cmd),
        )
        removed = remove_timers(mock_config)
        assert removed == ["dayz-task-t1"]
        assert not (unit_dir / "dayz-task-t1.timer").exists()
        assert not (unit_dir / "dayz-task-t1.service").exists()
        assert all("sudo" not in c for c in cmds)
        assert all(c[:2] == ["systemctl", "--user"] for c in cmds)


# ---------------------------------------------------------------------------
# Windows schtasks generation
# ---------------------------------------------------------------------------


class TestCronToSchtasksTriggers:
    def test_every_4_hours(self):
        result = cron_to_schtasks_triggers("0 */4 * * *")
        assert "CalendarTrigger" in result
        assert "PT4H" in result

    def test_daily_at_4am(self):
        result = cron_to_schtasks_triggers("0 4 * * *")
        assert "T04:00:00" in result
        assert "ScheduleByDay" in result

    def test_every_5_minutes(self):
        result = cron_to_schtasks_triggers("*/5 * * * *")
        assert "PT5M" in result

    def test_monday_at_4am(self):
        result = cron_to_schtasks_triggers("0 4 * * 1")
        assert "Monday" in result
        assert "ScheduleByWeek" in result

    def test_weekday_range(self):
        result = cron_to_schtasks_triggers("0 4 * * 1-5")
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            assert day in result

    def test_day_of_month(self):
        result = cron_to_schtasks_triggers("0 4 1 * *")
        assert "ScheduleByMonth" in result
        assert "DaysOfMonth" in result

    def test_all_stars(self):
        result = cron_to_schtasks_triggers("* * * * *")
        assert "CalendarTrigger" in result
        assert "ScheduleByDay" in result

    def test_output_is_valid_xml(self):
        import xml.etree.ElementTree as ET

        for expr in ["0 */4 * * *", "0 4 * * *", "*/5 * * * *", "0 4 * * 1"]:
            ET.fromstring(cron_to_schtasks_triggers(expr))

    def test_month_step_does_not_crash(self):
        # "1 */2 *" → 1st of every 2nd month. Previously int("*/2") raised
        # ValueError while mapping the month field.
        result = cron_to_schtasks_triggers("0 0 1 */2 *")
        for m in ("January", "March", "May", "July", "September", "November"):
            assert m in result
        assert "February" not in result

    def test_day_step_expands_to_real_days(self):
        # "*/2 * *" → every 2nd day. Previously emitted an invalid <Day>*/2</Day>.
        result = cron_to_schtasks_triggers("0 0 */2 * *")
        assert "*/2" not in result
        assert "<ns0:Day>1</ns0:Day>" in result or "<Day>1</Day>" in result

    def test_weekday_step_expands(self):
        result = cron_to_schtasks_triggers("0 4 * * */2")
        for day in ("Sunday", "Tuesday", "Thursday", "Saturday"):
            assert day in result

    def test_step_patterns_are_valid_xml(self):
        import xml.etree.ElementTree as ET

        for expr in ["0 0 1 */2 *", "0 0 */2 * *", "0 4 * * */2", "0 4 1-15/3 * *"]:
            ET.fromstring(cron_to_schtasks_triggers(expr))


def _triggers(expr: str) -> list[dict]:
    """The <Triggers> block as plain data: per trigger its start, repetition
    and schedule, with the namespace stripped."""
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
    root = ET.fromstring(cron_to_schtasks_triggers(expr))
    assert root.tag == f"{ns}Triggers"
    out = []
    for trig in root.findall(f"{ns}CalendarTrigger"):
        rep = trig.find(f"{ns}Repetition")
        sched = trig[-1]
        out.append({
            "start": trig.find(f"{ns}StartBoundary").text[11:16],
            "repeat": None if rep is None else (
                rep.find(f"{ns}Interval").text, rep.find(f"{ns}Duration").text
            ),
            "kind": sched.tag[len(ns):],
            "schedule": {
                child.tag[len(ns):]: [g.text or g.tag[len(ns):] for g in child] or child.text
                for child in sched
            },
        })
    return out


_ALL_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class TestSchtasksTranslatesWhatSystemdDoes:
    """plans/68 B22. The forms the systemd side translates since B7 reached
    the Windows converter raw: ``L`` became ``<Day>L</Day>``, ``5#3`` an
    element named ``5#3`` (the XML failed to parse), a month name crashed
    ``int()``, a month restriction with a weekday or without a day was dropped,
    and an hour range or list became an invalid or single start time. Every
    expected layout here was also checked against croniter's firings over two
    years, reading the end of a repetition both inclusively and not."""

    def test_the_last_day_of_the_month(self):
        (t,) = _triggers("0 4 L * *")
        assert t["kind"] == "ScheduleByMonth"
        assert t["schedule"] == {"DaysOfMonth": ["Last"], "Months": _ALL_MONTH_NAMES}

    def test_an_nth_and_a_last_weekday(self):
        (t,) = _triggers("0 4 * * 5#3")
        assert t["kind"] == "ScheduleByMonthDayOfWeek"
        assert t["schedule"] == {
            "Weeks": ["3"], "DaysOfWeek": ["Friday"], "Months": _ALL_MONTH_NAMES,
        }
        (t,) = _triggers("0 4 * jan-mar L5")
        assert t["schedule"] == {
            "Weeks": ["Last"], "DaysOfWeek": ["Friday"], "Months": ["January", "February", "March"],
        }

    def test_month_names_and_a_month_without_a_day(self):
        (t,) = _triggers("0 4 * JAN,mar *")
        assert t["kind"] == "ScheduleByMonth"
        assert t["schedule"]["DaysOfMonth"] == [str(d) for d in range(1, 32)]
        assert t["schedule"]["Months"] == ["January", "March"]

    def test_weekdays_in_some_months_keep_the_months(self):
        (t,) = _triggers("0 4 * 6-8 mon")
        assert t["kind"] == "ScheduleByMonthDayOfWeek"
        assert t["schedule"] == {
            "Weeks": ["1", "2", "3", "4", "Last"],
            "DaysOfWeek": ["Monday"],
            "Months": ["June", "July", "August"],
        }

    def test_sunday_as_seven_and_a_range_through_it(self):
        (t,) = _triggers("0 4 * * 0-7")
        assert t["kind"] == "ScheduleByDay"
        (t,) = _triggers("0 4 * * 5-7")
        assert t["kind"] == "ScheduleByWeek"
        assert t["schedule"]["DaysOfWeek"] == ["Sunday", "Friday", "Saturday"]

    @pytest.mark.parametrize("cron, start, repeat", [
        ("0 */4 * * *", "00:00", ("PT4H", "PT22H")),
        ("*/5 * * * *", "00:00", ("PT5M", "PT23H57M30S")),
        ("0 4,16 * * *", "04:00", ("PT12H", "PT18H")),
        ("0 9-17 * * *", "09:00", ("PT1H", "PT8H30M")),
        ("*/15 9-17 * * *", "09:00", ("PT15M", "PT8H52M30S")),
        ("0 4 * * *", "04:00", None),
    ])
    def test_evenly_spaced_times_are_one_repeating_trigger(self, cron, start, repeat):
        # The repetition ends half an interval after the last start, so that
        # start is in whether Task Scheduler counts the end in or not.
        (t,) = _triggers(cron)
        assert (t["start"], t["repeat"]) == (start, repeat)

    def test_uneven_times_are_a_trigger_each(self):
        triggers = _triggers("5,10,40 3,7 * * *")
        assert [t["start"] for t in triggers] == [
            "03:05", "03:10", "03:40", "07:05", "07:10", "07:40",
        ]
        assert all(t["repeat"] is None and t["kind"] == "ScheduleByDay" for t in triggers)

    @pytest.mark.parametrize("cron, reason", [
        ("0 4 * * fri#5", "there is no fifth"),
        ("0,1,3 * * * *", "at most 48 start times a day"),
    ])
    def test_what_task_scheduler_cannot_say_is_refused_on_windows_only(self, cron, reason):
        with patch("app.server.scheduler.IS_WINDOWS", True):
            assert validate_cron(cron) is False
            (error,) = validate_task(ScheduledTask(id="t1", task_type="restart", cron=cron))
            assert error.startswith(f"Invalid cron expression: {cron} (Windows scheduled tasks")
            assert reason in error
        with patch("app.server.scheduler.IS_WINDOWS", False):
            assert validate_cron(cron) is True

    @patch("app.server.scheduler.IS_WINDOWS", True)
    def test_install_registers_each_valid_task_and_skips_a_refused_one(
        self, mock_config, monkeypatch
    ):
        import xml.etree.ElementTree as ET

        from app.server import scheduler

        mock_config.scheduled_tasks = [
            ScheduledTask(id="third_friday", task_type="backup", cron="0 4 * jan 5#3").to_dict(),
            ScheduledTask(id="fifth_friday", task_type="backup", cron="0 4 * * 5#5").to_dict(),
        ]
        calls: list[list[str]] = []
        monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: calls.append(argv))

        assert install_timers(mock_config) == ["dayz-task-third_friday"]

        xml_path = mock_config.paths.schtasks_dir / "dayz-task-third_friday.xml"
        assert calls == [
            ["schtasks", "/Create", "/XML", str(xml_path), "/TN", "dayz-task-third_friday", "/F"]
        ]
        ns = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
        by_dow = ET.parse(xml_path).getroot().find(
            f"{ns}Triggers/{ns}CalendarTrigger/{ns}ScheduleByMonthDayOfWeek"
        )
        assert [w.text for w in by_dow.find(f"{ns}Weeks")] == ["3"]
        assert [m.tag[len(ns):] for m in by_dow.find(f"{ns}Months")] == ["January"]
        assert not (mock_config.paths.schtasks_dir / "dayz-task-fifth_friday.xml").exists()


class TestSchtasksXMLGeneration:
    def test_generate_schtasks_xml(self):
        task = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")
        xml = generate_schtasks_xml(task)
        assert '<?xml' in xml
        assert "Task" in xml
        assert "-m app --yes restart" in xml
        assert "-m cli" not in xml
        assert "restart_4h" in xml
        assert "<ns0:Enabled>true</ns0:Enabled>" in xml or "Enabled>true<" in xml

    def test_xml_is_parseable(self):
        import xml.etree.ElementTree as ET

        task = ScheduledTask(id="t1", task_type="backup", cron="0 6 * * *")
        xml_str = generate_schtasks_xml(task)
        root = ET.fromstring(xml_str)
        assert root.tag.endswith("}Task") or root.tag == "Task"

    def test_generate_schtasks_files(self, mock_config):
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        add_task(mock_config, ScheduledTask(
            id="t2", task_type="backup", cron="0 6 * * *", enabled=False,
        ))

        files = generate_schtasks_files(mock_config)
        assert len(files) == 1
        assert files[0][0] == "dayz-task-t1"

    @patch("app.server.scheduler.IS_WINDOWS", True)
    def test_install_timers_to_dir_windows(self, mock_config, tmp_path):
        import xml.etree.ElementTree as ET

        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        dest = tmp_path / "schtasks"
        dest.mkdir()

        installed = install_timers(mock_config, dest_dir=dest)
        assert len(installed) == 1
        xml_path = dest / "dayz-task-t1.xml"
        assert xml_path.is_file()
        ET.parse(xml_path)

    @patch("app.server.scheduler.IS_WINDOWS", True)
    def test_remove_timers_cleans_xml_windows(self, mock_config, tmp_path):
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        dest = tmp_path / "schtasks"
        dest.mkdir()

        install_timers(mock_config, dest_dir=dest)
        assert (dest / "dayz-task-t1.xml").is_file()

        removed = remove_timers(mock_config, dest_dir=dest)
        assert len(removed) == 1
        assert not (dest / "dayz-task-t1.xml").exists()


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


class TestPresets:
    def test_all_presets_valid(self):
        for name, preset_data in SCHEDULE_PRESETS.items():
            task = ScheduledTask.from_dict(preset_data)
            errors = validate_task(task)
            assert errors == [], f"Preset {name} invalid: {errors}"


# ---------------------------------------------------------------------------
# RCON restart warnings
# ---------------------------------------------------------------------------


class TestRestartWarningState:
    def test_pending_warnings(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[15, 10, 5, 1])
        pending = state.get_pending_warnings(remaining_minutes=12)
        assert 15 in pending  # 12 <= 15
        assert 10 not in pending  # 12 > 10

    def test_pending_warnings_filtered_by_interval(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[15, 10, 5, 3, 1])
        # 5-minute restart cycle — 15 and 10 should be skipped
        pending = state.get_pending_warnings(remaining_minutes=5, restart_interval=5)
        assert 15 not in pending
        assert 10 not in pending
        assert 5 in pending
        # 3 and 1 haven't been reached yet (remaining=5 > 3)
        assert 3 not in pending
        assert 1 not in pending

        # At remaining=2, thresholds 5 and 3 fire (both <= restart_interval=5)
        pending2 = state.get_pending_warnings(remaining_minutes=2, restart_interval=5)
        assert 5 in pending2
        assert 3 in pending2

    def test_pending_warnings_no_interval(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[15, 10, 5])
        # No interval — all thresholds apply (backwards compatible)
        pending = state.get_pending_warnings(remaining_minutes=3)
        assert 15 in pending
        assert 10 in pending
        assert 5 in pending

    def test_mark_sent(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[10, 5])
        state.mark_sent(10)
        pending = state.get_pending_warnings(remaining_minutes=8)
        assert 10 not in pending  # already sent

    def test_reset(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[10, 5])
        state.mark_sent(10)
        state.mark_sent(5)
        assert state.all_sent is True
        state.reset()
        assert state.all_sent is False
        pending = state.get_pending_warnings(remaining_minutes=3)
        assert 10 in pending and 5 in pending

    def test_restart_detected(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState()
        state.mark_sent(15)
        state._last_uptime_secs = 10000.0
        assert state.check_restart_detected(100.0) is True  # uptime dropped
        assert 15 not in state._sent  # was reset

    def test_no_false_restart(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState()
        state._last_uptime_secs = 100.0
        assert state.check_restart_detected(200.0) is False

    def test_thresholds_descending(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[1, 15, 5, 10])
        assert state.thresholds == [15, 10, 5, 1]


class TestRunWarningCycle:
    @pytest.mark.asyncio
    async def test_fires_warnings(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[10, 5])
        mock_rcon = MagicMock()
        mock_rcon.is_connected = MagicMock(return_value=True)
        mock_rcon.send_message = AsyncMock()
        mock_config = MagicMock(restart_lock_at_minutes=0)

        fired = await run_warning_cycle(
            state, remaining_minutes=4, rcon=mock_rcon, config=mock_config
        )
        # One warning, the nearest: 10 is marked sent unsaid.
        assert fired == [5]
        assert mock_rcon.send_message.call_count == 1
        assert "5 minute" in mock_rcon.send_message.call_args.args[0]

    @pytest.mark.asyncio
    async def test_a_warning_waits_for_rcon_and_is_not_lost(self):
        """`rcon.is_connected` was tested without the
        call, so a warning sent before the panel's connection was up went
        nowhere and was marked sent."""
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[15, 10, 5])
        rcon = MagicMock()
        rcon.is_connected = MagicMock(return_value=False)
        rcon.send_message = AsyncMock()
        toasts: list[int] = []
        cfg = MagicMock(restart_lock_at_minutes=0)

        assert await run_warning_cycle(state, 8, rcon=rcon, config=cfg, notify_toast=toasts.append) == []
        assert toasts == [] and rcon.send_message.await_count == 0
        rcon.is_connected = MagicMock(return_value=True)
        assert await run_warning_cycle(state, 7, rcon=rcon, config=cfg, notify_toast=toasts.append) == [10]
        assert toasts == [7] and "7 minute" in rcon.send_message.call_args.args[0]

    @pytest.mark.asyncio
    async def test_the_last_minute_still_toasts_when_rcon_stays_down(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5, 1])
        rcon = MagicMock()
        rcon.is_connected = MagicMock(return_value=False)
        toasts: list[int] = []
        cfg = MagicMock(restart_lock_at_minutes=0)
        assert await run_warning_cycle(state, 1, rcon=rcon, config=cfg, notify_toast=toasts.append) == [1]
        assert toasts == [1]

    @pytest.mark.asyncio
    async def test_a_late_countdown_says_the_time_left_once(self):
        """A restart task due in 7 minutes told players
        "15 minutes" and "10 minutes" in the same second."""
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[15, 10, 5, 3, 1])
        mock_rcon = MagicMock()
        mock_rcon.is_connected = MagicMock(return_value=True)
        mock_rcon.send_message = AsyncMock()
        toasts: list[int] = []
        cfg = MagicMock(restart_lock_at_minutes=0)

        fired = await run_warning_cycle(
            state, remaining_minutes=7, rcon=mock_rcon, config=cfg, notify_toast=toasts.append
        )
        assert fired == [10]
        assert toasts == [7]
        assert mock_rcon.send_message.call_count == 1
        assert "7 minute" in mock_rcon.send_message.call_args.args[0]
        # The rest of the countdown is as before.
        assert await run_warning_cycle(state, 6, rcon=mock_rcon, config=cfg) == []
        assert await run_warning_cycle(state, 4, rcon=mock_rcon, config=cfg, notify_toast=toasts.append) == [5]
        assert toasts == [7, 5]

    @pytest.mark.asyncio
    async def test_fires_warnings_with_interval_filter(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[15, 10, 5, 1])
        mock_rcon = MagicMock()
        mock_rcon.is_connected = MagicMock(return_value=True)
        mock_rcon.send_message = AsyncMock()

        # 5-minute restart, remaining=4 — only 5 fires (15/10 filtered by interval)
        fired = await run_warning_cycle(
            state, remaining_minutes=4, rcon=mock_rcon, config=MagicMock(restart_lock_at_minutes=0),
            restart_interval=5,
        )
        assert 15 not in fired
        assert 10 not in fired
        assert 5 in fired
        assert 1 not in fired  # remaining=4 > 1, not reached yet
        assert mock_rcon.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_no_fire_when_not_reached(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5, 1])
        fired = await run_warning_cycle(
            state, remaining_minutes=10, rcon=None, config=MagicMock(restart_lock_at_minutes=0)
        )
        assert fired == []

    @pytest.mark.asyncio
    async def test_no_duplicate_fires(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[10, 5])
        mock_rcon = MagicMock()
        mock_rcon.is_connected = MagicMock(return_value=True)
        mock_rcon.send_message = AsyncMock()

        await run_warning_cycle(state, 4, rcon=mock_rcon, config=MagicMock(restart_lock_at_minutes=0))
        mock_rcon.send_message.reset_mock()
        await run_warning_cycle(state, 3, rcon=mock_rcon, config=MagicMock(restart_lock_at_minutes=0))
        assert mock_rcon.send_message.call_count == 0  # already sent both

    @pytest.mark.asyncio
    async def test_works_without_rcon(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        toasts: list[int] = []

        fired = await run_warning_cycle(
            state, 3, rcon=None, config=MagicMock(restart_lock_at_minutes=0),
            notify_toast=lambda t: toasts.append(t),
        )
        assert fired == [5]
        assert toasts == [3]  # the 5-minute warning, 3 minutes late, says what is left


class TestRestartWarningTemplate:
    """plans/51 A (N9): the template was formatted outside the ``try``, so a
    ``{mins}`` typo raised out of ``run_warning_cycle`` — no RCon message, no
    toast, no Discord post, for every warning."""

    @pytest.mark.asyncio
    async def test_a_broken_template_sends_the_default_wording(self):
        from app.rcon.rcon_scheduler import DEFAULT_TEMPLATE, send_restart_warning

        rcon = MagicMock()
        rcon.send_message = AsyncMock()
        assert await send_restart_warning(rcon, 5, "Restart in {mins}") is True
        rcon.send_message.assert_awaited_once_with(DEFAULT_TEMPLATE.format(minutes=5))

    @pytest.mark.asyncio
    async def test_the_rest_of_the_cycle_still_runs(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5], template="Restart in {minutes")
        rcon = MagicMock()
        rcon.is_connected = MagicMock(return_value=True)
        rcon.send_message = AsyncMock()
        toasts: list[int] = []

        fired = await run_warning_cycle(
            state, 3, rcon=rcon, config=MagicMock(restart_lock_at_minutes=0),
            notify_toast=lambda t: toasts.append(t),
        )
        assert fired == [5]
        assert toasts == [3]  # the 5-minute warning, 3 minutes late, says what is left
        rcon.send_message.assert_awaited_once()


class TestTaskCommandsAreReal:
    """Guard against the scheduler generating commands the CLI doesn't have.

    The generated unit invokes ``python -m app --yes <command>``. Every entry
    in TASK_COMMANDS must therefore resolve to a real Click command path, and
    the module must be ``app`` (not the long-gone ``cli`` module).
    """

    def test_every_task_command_resolves(self):
        import click

        from app.__main__ import main

        ctx = click.Context(main)
        for task_type, command in TASK_COMMANDS.items():
            node: click.Command = main
            for part in command.split():
                if part.startswith("--"):
                    opts = {opt for param in node.params for opt in param.opts}
                    assert part in opts, f"{task_type}: '{part}' is not an option of '{command}'"
                    continue
                assert isinstance(node, click.Group), (
                    f"{task_type}: '{part}' has no parent group in '{command}'"
                )
                node = node.get_command(ctx, part)
                assert node is not None, (
                    f"{task_type}: '{part}' is not a real CLI command "
                    f"(TASK_COMMANDS['{task_type}'] = '{command}')"
                )

    def test_nonrestart_service_invokes_app_module_as_user(self):
        import getpass

        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        content = generate_service_content(task, MagicMock(instance_id=""))
        assert "-m app --yes backup create" in content
        assert f"User={getpass.getuser()}" in content
        assert "-m cli" not in content

    def test_restart_service_runs_as_root_systemctl(self):
        # A non-root timer can't restart the system unit and sudo has no tty, so
        # the restart oneshot runs as root (no User=) and calls systemctl
        # directly — no sudo, no nested zsm invocation.
        task = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")
        content = generate_service_content(task, MagicMock())
        assert "systemctl restart dayz-server.service" in content
        assert "sudo" not in content
        assert "User=" not in content  # defaults to root for a system unit
        assert "-m cli" not in content

    def test_nonrestart_service_user_scope_drops_user_line(self):
        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        content = generate_service_content(task, MagicMock(service_scope="user", instance_id=""))
        assert "-m app --yes backup create" in content
        assert "User=" not in content  # invalid in the per-user manager

    def test_restart_service_user_scope_uses_user_systemctl(self):
        # In user scope there is no root special case: the restart oneshot is a
        # normal user unit running `systemctl --user restart`.
        task = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")
        content = generate_service_content(task, MagicMock(service_scope="user"))
        assert "systemctl --user restart dayz-server.service" in content
        assert (
            "ExecCondition=/bin/sh -c 'systemctl --user is-active --quiet dayz-server.service"
            " || systemctl --user is-failed --quiet dayz-server.service'"
        ) in content
        assert "sudo" not in content
        assert "User=" not in content

    def test_schtasks_arguments_invoke_app_module(self):
        from app.server.scheduler import _working_dir

        for task_type, command in TASK_COMMANDS.items():
            task = ScheduledTask(id=f"t_{task_type}", task_type=task_type, cron="0 4 * * *")
            xml = generate_schtasks_xml(task)
            assert f"-m app --yes {command}" in xml
            assert "-m cli" not in xml
            assert f">{_working_dir()}<" in xml

    def test_the_generated_command_starts_from_its_working_directory(self):
        """Measured in 2026 Q3: with WorkingDirectory=<site-packages>/app,
        `python -m app` found zsm's own app/xml/ before the standard library's
        xml, and every task died at import with `No module named 'xml.dom'`."""
        import subprocess
        import sys

        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        lines = generate_service_content(task, MagicMock()).splitlines()
        workdir = next(ln.split("=", 1)[1] for ln in lines if ln.startswith("WorkingDirectory="))
        exec_start = next(ln.split("=", 1)[1] for ln in lines if ln.startswith("ExecStart="))
        assert exec_start.startswith(f"{sys.executable} -m app ")

        run = subprocess.run(
            [sys.executable, "-m", "app", "--version"],
            cwd=workdir, capture_output=True, text=True, timeout=120,
        )
        assert run.returncode == 0, run.stderr


class TestTaskUnitPaths:
    """plans/68 B19: the interpreter and the working directory reach the task
    unit as systemd reads them, and a normal path renders as it always did."""

    @pytest.fixture()
    def paths(self, monkeypatch):
        from app.server import scheduler

        def use(python: str, workdir: str, systemctl: str = "/usr/bin/systemctl"):
            monkeypatch.setattr(scheduler, "_python_exe", lambda: python)
            monkeypatch.setattr(scheduler, "_working_dir", lambda: Path(workdir))
            monkeypatch.setattr(scheduler.shutil, "which", lambda name: systemctl)
            monkeypatch.setattr("getpass.getuser", lambda: "zsm")

        return use

    @staticmethod
    def _unit_lines(text: str) -> dict[str, str]:
        return dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln)

    def test_a_normal_path_renders_byte_identical(self, paths):
        # Quoting every render would mark every installed timer outdated.
        paths(
            "/home/zsm/.local/share/uv/tools/zsm/bin/python",
            "/home/zsm/.local/share/uv/tools/zsm/lib/python3.12/site-packages",
        )
        backup = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        restart = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")

        assert generate_service_content(backup, MagicMock(instance_id="")) == (
            "[Unit]\n"
            "Description=DayZ Task: Quick Backup (backup_6h)\n"
            "After=network.target\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "User=zsm\n"
            "ExecStart=/home/zsm/.local/share/uv/tools/zsm/bin/python -m app --yes backup create\n"
            "WorkingDirectory=/home/zsm/.local/share/uv/tools/zsm/lib/python3.12/site-packages\n"
        )
        assert generate_service_content(restart, MagicMock(instance_id="")) == (
            "[Unit]\n"
            "Description=DayZ Task: Server Restart (restart_4h)\n"
            "After=network.target\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecCondition=/bin/sh -c 'systemctl is-active --quiet dayz-server.service"
            " || systemctl is-failed --quiet dayz-server.service'\n"
            "ExecStartPre=-/usr/bin/systemctl reset-failed dayz-server.service\n"
            "ExecStart=/usr/bin/systemctl restart dayz-server.service\n"
        )

    def test_a_path_with_a_space_stays_one_word(self, paths):
        import shlex

        python = "/opt/My Tools/100%/bin/python"
        workdir = "/opt/My Tools/site %h"
        paths(python, workdir, systemctl="/opt/sys tools/systemctl")
        backup = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        restart = ScheduledTask(id="restart_4h", task_type="restart", cron="0 */4 * * *")

        lines = self._unit_lines(generate_service_content(backup, MagicMock(instance_id="")))
        assert lines["ExecStart"] == '"/opt/My Tools/100%%/bin/python" -m app --yes backup create'
        # systemd reads %% as %, and splits on whitespace outside quotes.
        assert shlex.split(lines["ExecStart"].replace("%%", "%")) == [
            python, "-m", "app", "--yes", "backup", "create",
        ]
        # WorkingDirectory= is one value to the end of the line: never quoted.
        assert lines["WorkingDirectory"] == "/opt/My Tools/site %%h"

        lines = self._unit_lines(generate_service_content(restart, MagicMock(instance_id="")))
        assert lines["ExecStart"] == '"/opt/sys tools/systemctl" restart dayz-server.service'
        assert lines["ExecStartPre"] == '-"/opt/sys tools/systemctl" reset-failed dayz-server.service'

    @pytest.mark.parametrize("bad", ['/opt/a"b/python', "/opt/a\\b/python", "/opt/a\nb/python"])
    def test_a_path_systemd_cannot_hold_is_refused_and_nothing_is_written(
        self, paths, mock_config, tmp_path, bad
    ):
        from app.server.scheduler import timer_install_state
        from app.server.service_control import ServiceUserError

        paths(bad, "/opt/site-packages")
        add_task(mock_config, ScheduledTask(id="t1", task_type="backup", cron="0 4 * * *"))
        dest = tmp_path / "units"
        dest.mkdir()

        with pytest.raises(ServiceUserError, match="Python interpreter"):
            install_timers(mock_config, dest_dir=dest)
        assert list(dest.iterdir()) == []
        # The Scheduler page reads the same render: it must not raise.
        (dest / "dayz-task-t1.timer").write_text("old", encoding="utf-8")
        assert timer_install_state(mock_config, unit_dir=dest) == ({"t1": "outdated"}, [])

    @pytest.mark.parametrize("sep", ["\x0b", "\x85", " ", " "])
    def test_a_line_separator_in_either_path_is_refused(self, paths, sep):
        """plans/68 LB-7: the panel's, the server's and these units share the
        rule ``str.splitlines`` needs, since a user unit is rebuilt with it."""
        from app.server.service_control import ServiceUserError

        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        config = MagicMock(instance_id="", service_scope="user")
        for python, workdir in ((f"/opt/a{sep}b/python", "/opt/site"), ("/opt/python", f"/opt/a{sep}b")):
            paths(python, workdir)
            with pytest.raises(ServiceUserError):
                generate_service_content(task, config)

    def test_a_tab_is_quoted_not_refused(self, paths):
        import shlex

        paths("/opt/My\tTools/bin/python", "/opt/site")
        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")

        lines = self._unit_lines(generate_service_content(task, MagicMock(instance_id="")))

        assert shlex.split(lines["ExecStart"])[0] == "/opt/My\tTools/bin/python"


# ---------------------------------------------------------------------------
# Counting down to a scheduled restart (TODO C3b)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("timers_unknown")
class TestNextRestartOccurrence:
    def _config(self, tasks):
        return MagicMock(scheduled_tasks=tasks)

    def test_a_task_whose_timer_is_not_installed_is_skipped(self, monkeypatch):
        """Night test 2026-11-22: "Next restart" counted down to a task
        before Install timers, so it would never fire."""
        from app.server import scheduler

        monkeypatch.setattr(
            scheduler, "timer_install_state", lambda config, unit_dir=None: ({"r": scheduler.TIMER_MISSING}, [])
        )
        config = self._config([{"id": "r", "task_type": "restart", "cron": "0 6 * * *", "enabled": True}])
        now = datetime(2026, 9, 20, 21, 50, tzinfo=timezone.utc)

        assert scheduler.next_restart_occurrence(config, now=now) is None

    def test_returns_the_soonest_restart_and_its_cycle(self):
        from app.server.scheduler import next_restart_occurrence

        config = self._config([
            {"id": "nightly", "task_type": "restart", "cron": "0 6 * * *", "enabled": True},
            {"id": "every4h", "task_type": "restart", "cron": "0 */4 * * *", "enabled": True},
        ])
        now = datetime(2026, 9, 20, 21, 50, tzinfo=timezone.utc)

        when, cycle = next_restart_occurrence(config, now=now)
        assert when == datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
        assert cycle == 240  # the 4-hourly task, not the nightly one

    def test_only_restart_types_count(self):
        from app.server.scheduler import next_restart_occurrence

        config = self._config([
            {"id": "b", "task_type": "backup", "cron": "*/5 * * * *", "enabled": True},
            {"id": "r", "task_type": "restart", "cron": "0 6 * * *", "enabled": True},
        ])
        now = datetime(2026, 9, 20, 21, 50, tzinfo=timezone.utc)

        when, cycle = next_restart_occurrence(config, now=now)
        assert when == datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc)
        assert cycle == 1440

    def test_disabled_tasks_are_ignored(self):
        from app.server.scheduler import next_restart_occurrence

        config = self._config([
            {"id": "r", "task_type": "restart", "cron": "0 6 * * *", "enabled": False},
        ])
        assert next_restart_occurrence(config) is None

    def test_no_restart_task_means_no_occurrence(self):
        from app.server.scheduler import next_restart_occurrence

        assert next_restart_occurrence(self._config([])) is None

    def test_an_invalid_cron_does_not_hide_a_valid_one(self):
        from app.server.scheduler import next_restart_occurrence

        config = self._config([
            {"id": "bad", "task_type": "restart", "cron": "not a cron", "enabled": True},
            {"id": "ok", "task_type": "restart", "cron": "0 6 * * *", "enabled": True},
        ])
        now = datetime(2026, 9, 20, 21, 50, tzinfo=timezone.utc)

        when, _ = next_restart_occurrence(config, now=now)
        assert when == datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc)


class TestCronIsLocalTime:
    """systemd's OnCalendar and Windows' scheduled task both read the cron as
    local wall-clock, so this module has to as well — it used to anchor
    croniter at UTC and be a whole timezone offset out."""

    def test_next_run_matches_the_local_wall_clock(self, monkeypatch):
        import app.server.scheduler as sched

        # A fixed "now" at 21:50 in a UTC+2 zone: the next 06:00 firing is
        # 06:00 in that same zone, not 06:00 UTC (which is 08:00 there).
        plus_two = timezone(timedelta(hours=2))
        monkeypatch.setattr(
            sched, "cron_now", lambda: datetime(2026, 9, 20, 21, 50, tzinfo=plus_two)
        )

        nxt = ScheduledTask(id="t", task_type="restart", cron="0 6 * * *").next_run
        assert nxt == datetime(2026, 9, 21, 6, 0, tzinfo=plus_two)

    def test_oncalendar_and_next_run_name_the_same_hour(self):
        task = ScheduledTask(id="t", task_type="restart", cron="0 6 * * *")
        assert "06:00:00" in cron_to_oncalendar(task.cron)
        assert task.next_run.hour == 6

    @pytest.mark.parametrize(
        ("tz", "expected"),
        [
            (timezone.utc, "UTC"),
            (timezone(timedelta(hours=2), "CEST"), "CEST (UTC+2)"),
            (timezone(timedelta(hours=5, minutes=30)), "UTC+5:30"),
            (timezone(timedelta(hours=-3), "-03"), "UTC-3"),
        ],
    )
    def test_the_page_names_the_zone_cron_is_read_in(self, tz, expected):
        from app.server.scheduler import cron_time_zone

        assert cron_time_zone(datetime(2026, 10, 13, 4, 0, tzinfo=tz)) == expected


class TestCycleRollover:
    def test_a_climbing_countdown_starts_a_new_cycle(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[15, 10, 5, 1])
        state.check_cycle_rollover(15)
        state.mark_sent(15)
        state.check_cycle_rollover(5)
        state.mark_sent(5)

        # The restart window passed (whether or not the server actually went
        # down) and the countdown restarted at the top of the next cycle.
        assert state.check_cycle_rollover(240) is True
        assert state.all_sent is False
        assert 15 in state.get_pending_warnings(remaining_minutes=12)

    def test_a_falling_countdown_is_not_a_rollover(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[15, 10])
        state.check_cycle_rollover(15)
        state.mark_sent(15)
        assert state.check_cycle_rollover(11) is False
        assert 15 not in state.get_pending_warnings(remaining_minutes=11)

    def test_nothing_sent_yet_is_never_a_rollover(self):
        from app.rcon.rcon_scheduler import RestartWarningState

        state = RestartWarningState(thresholds=[15])
        state.check_cycle_rollover(5)
        assert state.check_cycle_rollover(240) is False


class TestJoinLock:
    """restart_lock_at_minutes: #lock inside the window, #unlock outside it."""

    @staticmethod
    def _rcon(connected=True):
        rcon = MagicMock()
        rcon.is_connected = MagicMock(return_value=connected)
        rcon.send_message = AsyncMock()
        rcon.send_command = AsyncMock()
        return rcon

    @staticmethod
    def _commands(rcon):
        return [c.args[0] for c in rcon.send_command.call_args_list]

    @pytest.mark.asyncio
    async def test_locks_once_inside_the_window(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        rcon = self._rcon()
        config = MagicMock(restart_lock_at_minutes=2)
        for remaining in (10, 3, 2, 1, 0):
            await run_warning_cycle(state, remaining, rcon=rcon, config=config)
        assert self._commands(rcon) == ["#lock"]
        assert state.locked is True

    @pytest.mark.asyncio
    async def test_zero_never_locks(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        rcon = self._rcon()
        await run_warning_cycle(state, 0, rcon=rcon, config=MagicMock(restart_lock_at_minutes=0))
        assert rcon.send_command.call_count == 0

    @pytest.mark.asyncio
    async def test_a_restart_that_did_not_happen_unlocks(self):
        """A deferred update: the countdown climbs to the next cycle and the
        server is still up, so it must not stay locked."""
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        rcon = self._rcon()
        config = MagicMock(restart_lock_at_minutes=2)
        await run_warning_cycle(state, 1, rcon=rcon, config=config)
        await run_warning_cycle(state, 360, rcon=rcon, config=config)
        assert self._commands(rcon) == ["#lock", "#unlock"]
        assert state.locked is False

    @pytest.mark.asyncio
    async def test_a_restart_that_happened_needs_no_unlock(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        state.check_restart_detected(3600)
        rcon = self._rcon()
        config = MagicMock(restart_lock_at_minutes=2)
        await run_warning_cycle(state, 1, rcon=rcon, config=config)
        state.check_restart_detected(30)  # the server came back
        await run_warning_cycle(state, 360, rcon=rcon, config=config)
        assert self._commands(rcon) == ["#lock"]

    @pytest.mark.asyncio
    async def test_a_failed_unlock_is_tried_again(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        rcon = self._rcon()
        config = MagicMock(restart_lock_at_minutes=2)
        await run_warning_cycle(state, 1, rcon=rcon, config=config)
        rcon.send_command.side_effect = RuntimeError("dropped")
        await run_warning_cycle(state, 360, rcon=rcon, config=config)
        assert state.locked is True
        rcon.send_command.side_effect = None
        await run_warning_cycle(state, 359, rcon=rcon, config=config)
        assert self._commands(rcon)[-1] == "#unlock"
        assert state.locked is False

    @pytest.mark.asyncio
    async def test_nothing_is_sent_without_rcon(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        rcon = self._rcon(connected=False)
        await run_warning_cycle(state, 1, rcon=rcon, config=MagicMock(restart_lock_at_minutes=2))
        assert rcon.send_command.call_count == 0
        assert state.locked is False

    @pytest.mark.asyncio
    async def test_a_window_as_long_as_the_cycle_is_skipped(self):
        from app.rcon.rcon_scheduler import RestartWarningState, run_warning_cycle

        state = RestartWarningState(thresholds=[5])
        rcon = self._rcon()
        await run_warning_cycle(
            state, 1, rcon=rcon, config=MagicMock(restart_lock_at_minutes=10),
            restart_interval=10,
        )
        assert rcon.send_command.call_count == 0

    def test_off_by_default(self):
        from app.shared.config import DayzConfig

        assert DayzConfig().restart_lock_at_minutes == 0


# ---------------------------------------------------------------------------
# Payload task types — announce / rcon (TODO C4)
# ---------------------------------------------------------------------------


class TestTaskUnitsNameTheirInstance:
    """plans/44 step 5. A unit without `--instance` loads whatever the default
    is when the timer fires, so `zsm instances use pvp` would point main's
    scheduled update at pvp's config."""

    def test_the_unit_passes_its_instance(self):
        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        content = generate_service_content(task, MagicMock(service_scope="user", instance_id="pvp"))
        assert "-m app --yes --instance pvp backup create" in content

    def test_a_config_on_the_legacy_layout_passes_none(self):
        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        content = generate_service_content(task, MagicMock(service_scope="user", instance_id=""))
        assert "--instance" not in content

    def test_the_windows_task_passes_it_too(self):
        from app.server.scheduler import generate_schtasks_xml

        task = ScheduledTask(id="backup_6h", task_type="backup", cron="0 */6 * * *")
        xml = generate_schtasks_xml(task, MagicMock(instance_id="main"))
        assert "-m app --yes --instance main backup create" in xml

    def test_the_command_it_runs_is_a_real_one(self):
        """The pinned argv parses as the root group's option, not a subcommand."""
        from click.testing import CliRunner

        from app.__main__ import main

        result = CliRunner().invoke(main, ["--yes", "--instance", "nope", "backup", "--help"])
        assert "no instance named 'nope'" in result.output


class TestPayloadTasks:
    def _task(self, task_type="announce", **options):
        return ScheduledTask(
            id="nightly-say", task_type=task_type, cron="0 20 * * *", options=options
        )

    def test_the_payload_never_reaches_the_unit(self):
        """The whole reason `scheduler run <id>` exists.

        `ExecStart=` is parsed by systemd, which has its own quoting rules and
        `%` specifiers — a message carrying either would be mangled or refuse
        to parse. The id is `_SAFE_ID_RE`-validated, so it is safe as argv.
        """
        task = self._task(message="100% uptime — say \"hi\"")
        content = generate_service_content(task, MagicMock(service_scope="user", instance_id=""))

        assert "-m app --yes scheduler run nightly-say" in content
        assert "100%" not in content
        assert "hi" not in content

    def test_the_windows_task_gets_the_same_treatment(self):
        task = self._task(message="100% uptime")
        xml = generate_schtasks_xml(task)
        assert "-m app --yes scheduler run nightly-say" in xml
        assert "100%" not in xml

    def test_task_command_leaves_other_types_alone(self):
        from app.server.scheduler import task_command

        plain = ScheduledTask(id="b", task_type="backup", cron="0 4 * * *")
        assert task_command(plain) == "backup create"
        assert task_command(self._task(message="x")) == "scheduler run nightly-say"

    def test_a_payload_task_without_its_payload_is_invalid(self):
        assert validate_task(self._task()) == ["Announcement tasks need a message"]
        assert validate_task(self._task("rcon")) == ["RCon Command tasks need a command"]

    def test_a_blank_payload_is_not_a_payload(self):
        assert validate_task(self._task(message="   ")) != []

    def test_a_newline_is_refused(self):
        """It would split one RCon command into two — the closest thing to
        injection this path has."""
        errors = validate_task(self._task("rcon", command="#lock\n#shutdown"))
        assert errors == ["command must be a single line"]

    def test_an_overlong_payload_is_refused(self):
        from app.server.scheduler import MAX_PAYLOAD_CHARS

        errors = validate_task(self._task(message="x" * (MAX_PAYLOAD_CHARS + 1)))
        assert "longer than" in errors[0]

    def test_a_valid_payload_task_passes(self):
        assert validate_task(self._task(message="Restart in 15 minutes")) == []
        assert validate_task(self._task("rcon", command="#lock")) == []

    def test_task_payload_reads_the_right_key_per_type(self):
        from app.server.scheduler import task_payload

        assert task_payload(self._task(message="  hello  ")) == "hello"
        assert task_payload(self._task("rcon", command="#lock")) == "#lock"
        # A key belonging to the other type is not read.
        assert task_payload(self._task(command="#lock")) == ""
        assert task_payload(ScheduledTask(id="b", task_type="backup", cron="0 4 * * *")) == ""

    def test_announce_and_rcon_do_not_stop_the_server(self):
        from app.server.scheduler import RESTART_TASK_TYPES

        assert "announce" not in RESTART_TASK_TYPES
        assert "rcon" not in RESTART_TASK_TYPES


# ---------------------------------------------------------------------------
# Which timers are actually installed (test pass 1, F42; plans/53 D)
# ---------------------------------------------------------------------------


class TestTimerInstallState:
    @pytest.fixture()
    def units(self, mock_config, tmp_path):
        from app.server.scheduler import timer_install_state

        dest = tmp_path / "units"
        dest.mkdir()
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        add_task(mock_config, ScheduledTask(id="t2", task_type="backup", cron="0 5 * * *"))
        return dest, lambda: timer_install_state(mock_config, unit_dir=dest)

    def test_everything_installed(self, mock_config, units):
        dest, state = units
        install_timers(mock_config, dest_dir=dest)
        assert state() == ({"t1": "installed", "t2": "installed"}, [])

    def test_an_edit_add_and_delete_show_up(self, mock_config, units):
        dest, state = units
        install_timers(mock_config, dest_dir=dest)
        update_task(mock_config, "t1", cron="0 6 * * *")
        add_task(mock_config, ScheduledTask(id="t3", task_type="backup", cron="0 7 * * *"))
        remove_task(mock_config, "t2")

        assert state() == ({"t1": "outdated", "t3": "missing"}, ["dayz-task-t2"])

    def test_a_disabled_task_with_its_units_still_there_is_outdated(self, mock_config, units):
        dest, state = units
        install_timers(mock_config, dest_dir=dest)
        update_task(mock_config, "t2", enabled=False)

        assert state()[0]["t2"] == "outdated"
        install_timers(mock_config, dest_dir=dest)
        assert state() == ({"t1": "installed", "t2": "disabled"}, [])

    def test_install_removes_a_deleted_tasks_timer(self, mock_config, units):
        """A deleted nightly restart kept restarting the server: nothing
        removed its units."""
        dest, state = units
        install_timers(mock_config, dest_dir=dest)
        remove_task(mock_config, "t1")

        install_timers(mock_config, dest_dir=dest)

        assert not (dest / "dayz-task-t1.timer").exists()
        assert not (dest / "dayz-task-t1.service").exists()
        assert state() == ({"t2": "installed"}, [])

    def test_remove_takes_leftovers_too(self, mock_config, units):
        dest, state = units
        install_timers(mock_config, dest_dir=dest)
        remove_task(mock_config, "t1")

        assert remove_timers(mock_config, dest_dir=dest) == ["dayz-task-t2", "dayz-task-t1"]
        assert list(dest.iterdir()) == []

    def test_unknown_without_systemd(self, mock_config, monkeypatch):
        from app.server import scheduler

        monkeypatch.setattr(scheduler, "has_systemd", lambda: False)
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        assert scheduler.timer_install_state(mock_config) == ({}, [])

    async def test_async_install_stops_and_removes_stale_timers(
        self, mock_config, tmp_path, monkeypatch
    ):
        """The path the web job and the CLI take, in user scope (no sudo)."""
        import asyncio

        from app.server import scheduler

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        monkeypatch.setattr(scheduler, "has_systemd", lambda: True)
        mock_config.service_scope = "user"
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))
        add_task(mock_config, ScheduledTask(id="t2", task_type="backup", cron="0 5 * * *"))
        unit_dir = tmp_path / "xdg" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        for name in ("dayz-task-t2", "dayz-task-gone"):
            (unit_dir / f"{name}.timer").write_text("old")
            (unit_dir / f"{name}.service").write_text("old")
        update_task(mock_config, "t2", enabled=False)

        calls = []

        async def _exec(*argv, **_kw):
            calls.append(list(argv))

            class _Proc:
                returncode = 0

                async def wait(self):
                    return 0

            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)

        lines: list[str] = []
        assert await scheduler.install_timers_async(mock_config, lines.append) == ["dayz-task-t1"]

        assert sorted(p.name for p in unit_dir.iterdir()) == [
            "dayz-task-t1.service", "dayz-task-t1.timer",
        ]
        # The job's log says what changed (it was empty).
        assert "Installed dayz-task-t1.timer" in lines
        assert {l for l in lines if l.startswith("Removed")} == {
            "Removed dayz-task-t2.timer, its task is gone or turned off",
            "Removed dayz-task-gone.timer, its task is gone or turned off",
        }
        for name in ("dayz-task-gone", "dayz-task-t2"):
            assert ["systemctl", "--user", "stop", f"{name}.timer"] in calls
            assert ["systemctl", "--user", "disable", f"{name}.timer"] in calls
        assert all("sudo" not in c for c in calls)
        assert scheduler.timer_install_state(mock_config) == (
            {"t1": "installed", "t2": "disabled"}, [],
        )

    async def test_async_install_in_system_scope_writes_to_the_scopes_unit_dir(
        self, mock_config, tmp_path, monkeypatch
    ):
        """sudo tee and sudo rm take their paths from scope.unit_dir (plans/68 B9)."""
        import asyncio

        from app.server import scheduler
        from app.shared import systemd_scope

        unit_dir = tmp_path / "system"
        unit_dir.mkdir()
        (unit_dir / "dayz-task-gone.timer").write_text("old")
        monkeypatch.setattr(systemd_scope, "SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(scheduler, "has_systemd", lambda: True)
        monkeypatch.setattr(scheduler, "sudo_prefix", lambda: ["sudo"])
        add_task(mock_config, ScheduledTask(id="t1", task_type="restart", cron="0 4 * * *"))

        calls = []

        async def _exec(*argv, **_kw):
            calls.append(list(argv))

            class _Stdin:
                def write(self, _data):
                    pass

                async def drain(self):
                    pass

                def close(self):
                    pass

            class _Proc:
                returncode = 0
                stdin = _Stdin()

                async def wait(self):
                    return 0

            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)

        assert await scheduler.install_timers_async(mock_config) == ["dayz-task-t1"]

        assert ["sudo", "tee", str(unit_dir / "dayz-task-t1.timer")] in calls
        assert ["sudo", "tee", str(unit_dir / "dayz-task-t1.service")] in calls
        assert ["sudo", "rm", "-f", str(unit_dir / "dayz-task-gone.timer")] in calls
        assert not any("/etc/systemd/system" in arg for c in calls for arg in c)
