"""One SteamCMD at a time across processes (app/shared/steamcmd_lock.py).

The web panel's own guard is per process; these tests hold the lock from a
real second process, because that is the case the lock exists for.
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.shared import steamcmd_lock
from app.shared.steamcmd_lock import SteamcmdBusy

pytestmark = pytest.mark.skipif(steamcmd_lock.fcntl is None, reason="flock is POSIX-only")

_HOLDER = r"""
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
os.ftruncate(fd, 0)
os.pwrite(fd, f"pid {os.getpid()}: steamcmd.sh +login someone +app_update\n".encode(), 0)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


@contextmanager
def held_elsewhere(seconds: float = 30):
    """Another process holds the lock until the block ends (or *seconds*)."""
    path = steamcmd_lock.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path), str(seconds)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "held"
        yield proc
    finally:
        proc.kill()
        proc.wait()


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(steamcmd_lock, "POLL_SECONDS", 0.05)


# --- the lock itself -------------------------------------------------------------


def test_the_lock_lives_in_the_config_dir():
    from app.shared import paths

    assert steamcmd_lock.lock_path() == paths.config_dir() / "steamcmd.lock"


def test_another_process_holding_it_makes_a_non_waiting_caller_busy():
    with held_elsewhere():
        with pytest.raises(SteamcmdBusy) as exc:
            with steamcmd_lock.hold(["steamcmd.sh"], wait=0):
                pytest.fail("ran beside another SteamCMD")
    assert "Another SteamCMD is running (pid" in str(exc.value)
    assert "+app_update" in str(exc.value)


def test_a_bounded_wait_gives_up():
    with held_elsewhere():
        start = time.monotonic()
        with pytest.raises(SteamcmdBusy):
            with steamcmd_lock.hold(["steamcmd.sh"], wait=0.3):
                pass
        assert time.monotonic() - start >= 0.3


def test_an_unbounded_wait_runs_once_the_other_is_done():
    notices = []
    with held_elsewhere(seconds=0.5):
        start = time.monotonic()
        with steamcmd_lock.hold(["steamcmd.sh"], on_wait=notices.append):
            waited = time.monotonic() - start
    assert waited >= 0.2
    assert len(notices) == 1 and "Waiting for another SteamCMD" in notices[0]


def test_a_killed_holder_frees_it():
    with held_elsewhere() as proc:
        proc.kill()
        proc.wait()
        with steamcmd_lock.hold(["steamcmd.sh"], wait=0):
            pass


def test_the_holder_line_is_ours_while_held_and_gone_after():
    with steamcmd_lock.hold(["/opt/steamcmd/steamcmd.sh", "+login", "bob", "+quit"]):
        assert steamcmd_lock.holder() == f"pid {os.getpid()}: steamcmd.sh +login +quit"
    assert steamcmd_lock.holder() == ""


def test_the_holder_line_never_carries_the_account_a_password_or_a_code():
    label = steamcmd_lock._label(
        ["steamcmd.sh", "+login", "bob", "hunter2", "ABCDE", "+quit"]
    )
    assert label == "steamcmd.sh +login +quit"


def test_an_unwritable_config_dir_runs_unlocked(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", blocker)
    with steamcmd_lock.hold(["steamcmd.sh"], wait=0):
        pass


@pytest.mark.parametrize("cmd, expected", [
    (["/home/zsm/steamcmd/steamcmd.sh", "+quit"], True),
    (["steamcmd.exe", "+quit"], True),
    (["systemctl", "daemon-reload"], False),
    ([], False),
])
def test_is_steamcmd(cmd, expected):
    assert steamcmd_lock.is_steamcmd(cmd) is expected


def test_the_async_wait_gives_up_on_cancel():
    cancelled = {"now": False}

    async def attempt():
        async with steamcmd_lock.hold_async(["steamcmd.sh"], cancelled=lambda: cancelled["now"]):
            pytest.fail("ran beside another SteamCMD")

    async def main():
        task = asyncio.create_task(attempt())
        await asyncio.sleep(0.2)
        assert not task.done(), "it waits rather than failing"
        cancelled["now"] = True
        with pytest.raises(SteamcmdBusy):
            await task

    with held_elsewhere():
        asyncio.run(main())


# --- the callers ----------------------------------------------------------------------


@pytest.fixture
def steamcmd(sample_config, tmp_path):
    """A stand-in steamcmd.sh that records each run."""
    home = tmp_path / "steamcmd"
    home.mkdir()
    script = home / "steamcmd.sh"
    runs = tmp_path / "runs.txt"
    script.write_text(f'#!/bin/sh\necho "$@" >> {runs}\necho fake-steamcmd-output\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    sample_config.steamcmd_dir = str(home)
    sample_config.steam_username = "bob"
    return runs


def _job():
    from app.web.jobs import Job
    from app.web.ws import WsHub

    return Job(id="j", name="Update", kind="update", hub=WsHub())


def _runs(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def test_check_steam_login_can_refuse_to_wait(sample_config, steamcmd):
    from app.shared.steam import check_steam_login

    with held_elsewhere():
        with pytest.raises(SteamcmdBusy):
            check_steam_login(sample_config, wait=0)
    assert _runs(steamcmd) == []
    assert check_steam_login(sample_config) is True
    assert _runs(steamcmd) == ["+login bob +quit"]


_STUCK = """#!/bin/sh
sleep 60 &
echo $! > {pids}
echo stuck-at-a-prompt
wait
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it has exited all the same.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


@pytest.fixture()
def stuck_steamcmd(sample_config, tmp_path):
    """steamcmd.sh whose child outlives the wrapper, the way the real binary does."""
    home = tmp_path / "stuck-steamcmd"
    home.mkdir()
    pids = tmp_path / "child.pid"
    script = home / "steamcmd.sh"
    script.write_text(_STUCK.format(pids=pids))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    sample_config.steamcmd_dir = str(home)
    sample_config.steam_username = "bob"
    return pids


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc")
def test_a_timeout_ends_the_whole_steamcmd_run_before_the_lock_is_free(
    sample_config, stuck_steamcmd
):
    cmd = [str(sample_config.steamcmd_path), "+quit"]
    with steamcmd_lock.hold(cmd):
        with pytest.raises(subprocess.TimeoutExpired) as e:
            steamcmd_lock.run_to_end(cmd, timeout=1, capture=True)
        assert not _alive(int(stuck_steamcmd.read_text())), "the SteamCMD child outlived the timeout"
    assert b"stuck-at-a-prompt" in e.value.stdout


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc")
def test_check_steam_login_ends_the_child_on_a_timeout(sample_config, stuck_steamcmd):
    from app.shared.steam import check_steam_login

    assert check_steam_login(sample_config, timeout=1) is False
    assert not _alive(int(stuck_steamcmd.read_text()))


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc")
def test_a_passthrough_timeout_ends_the_steamcmd_child(sample_config, stuck_steamcmd):
    from app.shared.steam import run_passthrough

    with pytest.raises(subprocess.TimeoutExpired):
        run_passthrough([str(sample_config.steamcmd_path), "+quit"], timeout=1)
    assert not _alive(int(stuck_steamcmd.read_text()))


def test_the_guard_request_ends_the_whole_run_on_its_timeout(sample_config, steamcmd, monkeypatch):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    calls = []

    def _timed_out(cmd, *, timeout, capture=False):
        calls.append(timeout)
        raise subprocess.TimeoutExpired(cmd, timeout, output=b"Steam Guard code:", stderr=b"")

    monkeypatch.setattr(steamcmd_lock, "run_to_end", _timed_out)
    sample_config.web_password_hash = hash_password("testpass123")
    sample_config.save()
    with TestClient(server.create_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": "testpass123"})
        resp = client.post(
            "/api/steam/request-guard", json={"username": "bob", "password": "hunter2"}
        )
    # The panel's startup probe (check_steam_login, 90 s) runs first.
    assert calls[-1] == 120
    assert resp.status_code == 200, resp.text


def test_run_passthrough_waits_for_another_steamcmd(sample_config, steamcmd):
    from app.shared.steam import build_server_update_cmd, run_passthrough

    with held_elsewhere(seconds=0.5):
        start = time.monotonic()
        rc, out = run_passthrough(build_server_update_cmd(sample_config))
        waited = time.monotonic() - start
    assert rc == 0 and "fake-steamcmd-output" in out
    assert waited >= 0.2, "it ran while the other one held the lock"


def test_run_passthrough_leaves_other_commands_alone(monkeypatch):
    from app.shared.steam import run_passthrough

    monkeypatch.setattr(steamcmd_lock, "hold", lambda *a, **k: pytest.fail("locked"))
    rc, out = run_passthrough(["echo", "hi"])
    assert rc == 0 and "hi" in out


def test_a_web_job_waits_in_its_log_and_a_cancel_ends_the_wait(sample_config, steamcmd):
    from app.shared.steam import build_server_update_cmd
    from app.web.jobs import stream_subprocess

    async def main():
        job = _job()
        task = asyncio.create_task(stream_subprocess(job, build_server_update_cmd(sample_config)))
        await asyncio.sleep(0.3)
        assert not task.done()
        job.cancel_event.set()
        return job, await asyncio.wait_for(task, 5)

    with held_elsewhere():
        job, rc = asyncio.run(main())

    lines = "\n".join(line for _, line in job.log)
    assert rc == -15
    assert "Waiting for another SteamCMD to finish (pid" in lines
    assert "Cancelled while waiting" in lines
    assert job.ran_steamcmd
    assert _runs(steamcmd) == [], "SteamCMD never started"


def test_a_web_job_runs_once_the_other_is_done(sample_config, steamcmd):
    from app.shared.steam import build_server_update_cmd
    from app.web.jobs import stream_subprocess

    job = _job()
    with held_elsewhere(seconds=0.4):
        rc = asyncio.run(stream_subprocess(job, build_server_update_cmd(sample_config)))
    assert rc == 0
    assert "fake-steamcmd-output" in [line for _, line in job.log]
    assert len(_runs(steamcmd)) == 1


def test_the_panels_probe_serves_its_last_answer_while_another_process_runs_steamcmd(
    sample_config, steamcmd
):
    from app.web.state import WebState

    state = WebState(sample_config)
    state.note_steam_ok(False)
    state.steam_checked_at = 0  # stale: a probe would measure

    with held_elsewhere():
        assert asyncio.run(state.steam_probe(force=True)) is False
    assert _runs(steamcmd) == []


def test_the_guard_request_answers_409_while_another_steamcmd_runs(sample_config, steamcmd):
    """The one SteamCMD the panel runs outside a job: it must not queue."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    sample_config.save()
    with held_elsewhere(), TestClient(server.create_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": "testpass123"})
        resp = client.post(
            "/api/steam/request-guard", json={"username": "bob", "password": "hunter2"}
        )
    assert resp.status_code == 409
    assert "Another SteamCMD is running" in resp.json()["detail"]
    assert "hunter2" not in resp.text
    assert not [r for r in _runs(steamcmd) if "hunter2" in r]


def test_the_doctor_does_not_queue_a_second_steamcmd(sample_config, steamcmd):
    from app.commands import doctor

    with held_elsewhere():
        label, ok, detail = doctor._check_steam_login(sample_config)
    assert ok and detail.startswith("Not checked: Another SteamCMD is running")
    assert _runs(steamcmd) == []
