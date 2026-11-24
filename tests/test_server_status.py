"""Server uptime (app/server/server_status.py).

Test pass 1, F1: a stopped or failed unit keeps its last ActiveEnterTimestamp,
so the dashboard's uptime kept counting on a dead server.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.server import server_status


class _FakeScope:
    def __init__(self, out: str) -> None:
        self.out = out
        self.calls: list[tuple] = []

    def query(self, *args: str) -> str:
        self.calls.append(args)
        return self.out


def _started(minutes_ago: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%a %Y-%m-%d %H:%M:%S UTC")


@pytest.fixture()
def fake_scope(monkeypatch):
    monkeypatch.setattr("app.shared.platform.IS_WINDOWS", False)

    def install(out: str) -> _FakeScope:
        scope = _FakeScope(out)
        monkeypatch.setattr("app.shared.systemd_scope.get_scope", lambda config=None: scope)
        return scope

    return install


@pytest.mark.parametrize("state", ["failed", "inactive", "activating", "deactivating"])
def test_no_uptime_unless_the_unit_is_running(fake_scope, state):
    fake_scope(f"ActiveState={state}\nActiveEnterTimestamp={_started(40)}\n")
    assert server_status.get_server_uptime() is None


@pytest.mark.parametrize("state", ["active", "reloading"])
def test_a_running_unit_has_an_uptime(fake_scope, state):
    # systemctl prints properties in its own order, not the argument order.
    scope = fake_scope(f"ActiveEnterTimestamp={_started(40)}\nActiveState={state}\n")
    uptime = server_status.get_server_uptime()
    assert uptime is not None
    assert timedelta(minutes=39) < uptime < timedelta(minutes=41)
    assert scope.calls == [
        ("show", "dayz-server.service", "--property=ActiveState,ActiveEnterTimestamp")
    ]


def test_an_empty_timestamp_is_no_uptime(fake_scope):
    fake_scope("ActiveState=active\nActiveEnterTimestamp=\n")
    assert server_status.get_server_uptime() is None


def test_a_messages_file_that_does_not_parse_has_no_shutdown_deadline(sample_config, tmp_mission_dir):
    messages = tmp_mission_dir / "db" / "messages.xml"
    messages.write_text(
        "<messages><message><deadline>60</deadline><shutdown>1</shutdown></message></messages>",
        encoding="utf-8",
    )
    assert server_status.get_shutdown_deadline(sample_config) == 60

    messages.write_text("<messages><message><deadline>60</deadline>", encoding="utf-8")
    assert server_status.get_shutdown_deadline(sample_config) is None


# ── Windows: one PowerShell call per status tick (2026 Q4) ──────────────


class _FakeRun:
    """subprocess.run stand-in that answers the shared PowerShell snapshot."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        import subprocess

        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


@pytest.fixture()
def windows(monkeypatch):
    monkeypatch.setattr("app.shared.platform.IS_WINDOWS", True)
    monkeypatch.setattr(server_status, "IS_WINDOWS", True)
    monkeypatch.setattr(server_status, "_win_snapshot", {"at": None, "value": None})
    monkeypatch.setattr(server_status, "_win_cpu_last", {"cpu": None, "at": None})

    def install(stdout: str) -> _FakeRun:
        run = _FakeRun(stdout)
        monkeypatch.setattr(server_status.subprocess, "run", run)
        return run

    return install


def test_one_tick_on_windows_starts_one_process(windows):
    start = (datetime.now() - timedelta(minutes=10)).replace(microsecond=0)
    run = windows(f"{start.isoformat()}|2147483648|60.5\n")

    assert server_status.get_server_uptime() is not None
    assert server_status.read_server_memory() == "2.0 GB"
    assert server_status.read_server_cpu() is not None

    assert len(run.calls) == 1
    assert run.calls[0][:3] == ["powershell", "-NoProfile", "-NonInteractive"]


def test_windows_cpu_is_the_share_of_all_cores_between_two_readings(windows, monkeypatch):
    start = (datetime.now() - timedelta(minutes=10)).replace(microsecond=0)
    run = windows(f"{start.isoformat()}|1048576|100.0\n")
    clock = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr("os.cpu_count", lambda: 4)

    server_status.read_server_cpu()
    clock[0] += 10.0
    run.stdout = f"{start.isoformat()}|1048576|120.0\n"

    # 20 CPU seconds in 10 s on 4 cores is half the machine.
    assert server_status.read_server_cpu() == "50%"


def test_no_server_process_on_windows_reads_as_nothing(windows):
    windows("")
    assert server_status.get_server_uptime() is None
    assert server_status.read_server_memory() is None
    assert server_status.read_server_cpu() is None


def test_a_stopped_server_on_windows_is_not_asked_about(windows, monkeypatch, sample_config):
    from app.server import service_control
    from app.web.status_poller import collect_status

    run = windows("")
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)

    data = collect_status(sample_config)

    assert data["running"] is False
    assert data["uptime_seconds"] is None and data["memory"] is None and data["cpu"] is None
    assert run.calls == []


@pytest.mark.parametrize("zone", ["UTC", "-03", "+0530", "JST", "EST"])
def test_any_zone_systemd_prints_has_an_uptime(fake_scope, zone):
    # systemd prints the machine's own zone: an abbreviation strptime's %Z
    # knows only for UTC and this machine's names, or "-03" for a zone with
    # none (America/Sao_Paulo). Every other one read as no uptime at all.
    started = (datetime.now() - timedelta(minutes=40)).strftime("%a %Y-%m-%d %H:%M:%S")
    fake_scope(f"ActiveState=active\nActiveEnterTimestamp={started} {zone}\n")
    uptime = server_status.get_server_uptime()
    assert uptime is not None
    assert timedelta(minutes=39) < uptime < timedelta(minutes=41)
