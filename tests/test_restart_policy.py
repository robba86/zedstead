"""Tests for the restart policy setting and the retry countdown (plans/38).

The unit's `Restart=` / `RestartSec=` were hardcoded; a start that failed came
back thirty seconds later with nothing in zsm saying why, which is the
problem this fixes. Package A makes them settings, package B
makes the wait visible on the Start button.

Nothing here runs systemctl.
"""

from __future__ import annotations

import logging

import pytest

from app.server import service_control
from app.shared import systemd_scope


@pytest.fixture()
def unit_state(monkeypatch):
    """Answer `systemctl show` from a dict the test controls, never systemd."""
    state = {"out": ""}

    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(
        systemd_scope.SystemdScope, "query", lambda self, *a: state["out"]
    )
    return state


# ---------------------------------------------------------------------------
# A — the policy is a setting
# ---------------------------------------------------------------------------


def test_the_defaults_render_the_behaviour_that_was_hardcoded(sample_config):
    """An existing install's unit must not change meaning just because the
    values moved into the config."""
    text = service_control._render_service(sample_config)
    assert "Restart=always" in text
    assert "RestartSec=30" in text


def test_the_policy_and_delay_follow_the_config(sample_config):
    sample_config.service_restart_policy = "on-failure"
    sample_config.service_restart_seconds = 90
    text = service_control._render_service(sample_config)
    assert "Restart=on-failure" in text
    assert "RestartSec=90" in text


def test_user_scope_keeps_them(sample_config):
    """`_to_user_unit` filters lines; a filter that swallowed these would make
    the setting silently do nothing in the default scope."""
    sample_config.service_scope = "user"
    sample_config.service_restart_policy = "no"
    text = service_control._render_service(sample_config)
    assert "Restart=no" in text
    assert "RestartSec=30" in text
    assert "User=" not in text  # the thing _to_user_unit is actually for


def test_an_unknown_policy_falls_back_instead_of_writing_it(sample_config, caplog):
    """Validated at render time, not at save time: a unit systemd refuses to
    parse leaves the box with no way to start the server at all."""
    sample_config.service_restart_policy = "sometimes"
    with caplog.at_level(logging.WARNING):
        text = service_control._render_service(sample_config)
    assert "Restart=always" in text
    assert "sometimes" not in text
    assert any("service_restart_policy" in r.message for r in caplog.records)
    # The config keeps what was typed; only the unit is made sane.
    assert sample_config.service_restart_policy == "sometimes"


@pytest.mark.parametrize(
    "given,expected", [(0, 1), (-5, 1), (99999, 3600), ("x", 30), (None, 30)]
)
def test_an_out_of_range_delay_is_clamped(sample_config, given, expected):
    sample_config.service_restart_seconds = given
    assert f"RestartSec={expected}" in service_control._render_service(sample_config)


def test_both_fields_are_reachable_from_the_settings_surface():
    from app.shared.settings_registry import _BY_KEY

    policy = _BY_KEY["service_restart_policy"]
    assert policy.choices == ("always", "on-failure", "no")
    # The one thing a reader cannot guess has to be in the description.
    assert "egenerate" in policy.description
    assert "egenerate" in _BY_KEY["service_restart_seconds"].description


def test_the_container_does_not_pin_the_policy(sample_config, monkeypatch):
    """`sync_config_from_env` forces service_scope because PID-1 systemd has no
    user bus. The restart policy has no such constraint, and pinning it would
    make the setting silently ineffective in Docker — the exact failure the
    stale-unit warning exists to prevent."""
    from app.shared import container

    sample_config.service_restart_policy = "no"
    sample_config.service_scope = "user"
    sample_config.save()

    config, _created, _changed = container.sync_config_from_env({})
    assert config.service_restart_policy == "no"
    assert config.service_scope == "system"


# ---------------------------------------------------------------------------
# A3 — a setting that needs a regenerate has to say so
# ---------------------------------------------------------------------------


def test_a_unit_matching_the_settings_is_not_stale(sample_config, tmp_path, monkeypatch):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    monkeypatch.setattr(
        systemd_scope.SystemdScope, "unit_dir", property(lambda self: unit_dir)
    )
    (unit_dir / systemd_scope.SERVICE_NAME).write_text(
        service_control._render_service(sample_config), encoding="utf-8"
    )
    assert service_control.unit_is_stale(sample_config) is False


def test_changing_the_policy_makes_the_installed_unit_stale(
    sample_config, tmp_path, monkeypatch
):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    monkeypatch.setattr(
        systemd_scope.SystemdScope, "unit_dir", property(lambda self: unit_dir)
    )
    (unit_dir / systemd_scope.SERVICE_NAME).write_text(
        service_control._render_service(sample_config), encoding="utf-8"
    )
    sample_config.service_restart_policy = "no"
    assert service_control.unit_is_stale(sample_config) is True


def test_nothing_installed_is_not_stale(sample_config, tmp_path, monkeypatch):
    """Staleness has to be earned: with no unit there is nothing to compare,
    and "regenerate" would be advice about a file that does not exist."""
    unit_dir = tmp_path / "empty"
    unit_dir.mkdir()
    monkeypatch.setattr(
        systemd_scope.SystemdScope, "unit_dir", property(lambda self: unit_dir)
    )
    assert service_control.unit_is_stale(sample_config) is False
    assert service_control.service_setup_state(sample_config)["stale"] is False


# ---------------------------------------------------------------------------
# B — the countdown
#
# The deadline formula and the clock it is measured against were MEASURED
# (plans/38 B1, a throwaway Restart=always/RestartSec=15 unit), not derived
# from docs. See _seconds_until_restart.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30s", 30.0),
        ("15s", 15.0),
        ("1min 30s", 90.0),
        ("1h 5min", 3900.0),
        ("500ms", 0.5),
        ("0", 0.0),
        ("infinity", None),
        ("", None),
        ("nonsense", None),
    ],
)
def test_systemd_timespans_parse(text, expected):
    """`show -p RestartUSec` prints a formatted span, not a number. "ms" and
    "min" both start with "m", so a naive table reads "1min" as a
    millisecond — a countdown 60000 times too short."""
    assert service_control.parse_timespan(text) == expected


def _waiting(monotonic_us: int, restart: str = "30s") -> dict:
    return {
        "ActiveState": "activating",
        "SubState": "auto-restart",
        "NRestarts": "2",
        "Result": "exit-code",
        "RestartUSec": restart,
        "InactiveEnterTimestampMonotonic": str(monotonic_us),
    }


def test_the_countdown_is_measured_against_the_clock_systemd_used(monkeypatch):
    """`time.monotonic()`, not `/proc/uptime`. Measured on this box: systemd's
    monotonic read 144511s while uptime read 599540s, because uptime counts
    suspended time. Uptime would have made the countdown days wrong."""
    monkeypatch.setattr(service_control.time, "monotonic", lambda: 1000.0)
    assert service_control._seconds_until_restart(_waiting(1_000_000_000 - 0)) is not None
    # base 990s, +30s delay, now 1000s -> 20s left
    assert service_control._seconds_until_restart(_waiting(990_000_000)) == 20


def test_a_wait_that_has_elapsed_reads_as_zero_not_as_a_negative(monkeypatch):
    monkeypatch.setattr(service_control.time, "monotonic", lambda: 1000.0)
    assert service_control._seconds_until_restart(_waiting(900_000_000)) == 0


def test_an_unparseable_delay_gives_no_countdown(monkeypatch):
    monkeypatch.setattr(service_control.time, "monotonic", lambda: 1000.0)
    assert service_control._seconds_until_restart(_waiting(990_000_000, "infinity")) is None


def test_without_a_monotonic_base_it_falls_back_to_the_wall_clock(monkeypatch):
    values = _waiting(0)
    values["InactiveEnterTimestamp"] = "Mon 2026-09-21 16:55:18 UTC"
    monkeypatch.setattr(service_control.time, "time", lambda: 1790009728.0)
    # 16:55:18 UTC is epoch 1790009718; +30s delay, now +10s -> 20s left
    assert service_control._seconds_until_restart(values) == 20


def test_only_a_waiting_unit_gets_a_countdown(unit_state, sample_config):
    """A server somebody stopped is inactive/dead, not activating/auto-restart.
    Reporting a retry there would invent one."""
    unit_state["out"] = "\n".join(
        [
            "ActiveState=inactive",
            "SubState=dead",
            "NRestarts=0",
            "Result=success",
            "RestartUSec=30s",
            "InactiveEnterTimestampMonotonic=990000000",
        ]
    )
    stats = service_control.restart_stats(sample_config)
    assert stats["looping"] is False
    assert stats["seconds_left"] is None


def test_the_status_payload_carries_the_countdown(unit_state, sample_config, monkeypatch):
    from app.web.status_poller import collect_status

    monkeypatch.setattr(service_control.time, "monotonic", lambda: 1000.0)
    unit_state["out"] = "\n".join(
        [
            "ActiveState=activating",
            "SubState=auto-restart",
            "NRestarts=2",
            "Result=exit-code",
            "RestartUSec=30s",
            "InactiveEnterTimestampMonotonic=990000000",
        ]
    )
    data = collect_status(sample_config)
    assert data["restart_pending"] is True
    assert data["restart_in_seconds"] == 20
    # Not folded into next_restart_minutes: that is the scheduler's restart.
    assert "next_restart_minutes" in data


def test_windows_has_no_countdown(monkeypatch, sample_config):
    from app.web.status_poller import collect_status

    monkeypatch.setattr(service_control, "IS_WINDOWS", True)
    data = collect_status(sample_config)
    assert data["restart_pending"] is False
    assert data["restart_in_seconds"] is None


def test_the_poll_still_makes_one_show_call(unit_state, sample_config, monkeypatch):
    """This runs every 5 seconds and, in system scope, each call may go
    through `sudo -n`. The countdown must not have added a second one."""
    calls: list[tuple] = []

    def counting_query(self, *args):
        calls.append(args)
        return unit_state["out"]

    unit_state["out"] = "ActiveState=active\nSubState=running\nNRestarts=0\nResult=success"
    monkeypatch.setattr(systemd_scope.SystemdScope, "query", counting_query)
    service_control.restart_stats(sample_config)
    assert len(calls) == 1
    props = [a for a in calls[0] if a.startswith("--property=")]
    assert "--property=RestartUSec" in props
    assert "--property=InactiveEnterTimestampMonotonic" in props
