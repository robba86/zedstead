"""Tests for app/web/jobs.py (Job, JobManager, stream_subprocess, JobRunner)
and the /api/jobs, /api/update/*, /api/logs/rpt endpoints.

Router tests that start a job and poll it use `with TestClient(app) as
client:` deliberately: without the `with` block, TestClient opens a fresh
anyio portal (a throwaway event loop) per request, so a background
asyncio.create_task started during one request would never be seen running
by a later request. Inside the `with` block, TestClient reuses one portal
(one persistent event-loop thread) across all requests, so the job's task
keeps running between polls — see starlette.testclient's `_portal_factory`.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.shared.platform import IS_WINDOWS
from app.web.jobs import Job, JobConflictError, JobManager, JobRunner, strip_ansi, strip_markup
from app.web.ws import WsHub

PASSWORD = "testpass123"


def test_strip_markup():
    assert strip_markup("[red]Error[/red]: bad") == "Error: bad"
    assert strip_markup("no markup here") == "no markup here"


@pytest.mark.parametrize("raw, shown", [
    ("\x1b[0m@ShutdownOnFailedCommand", "@ShutdownOnFailedCommand"),
    ("\x1b[1;33mWarning:\x1b[0m low disk", "Warning: low disk"),
    ("\x1b]0;steamcmd\x07Loading Steam API...OK", "Loading Steam API...OK"),
    ("\x1b[2K\x1b[1GUpdate state (0x61) downloading", "Update state (0x61) downloading"),
    ("cut short\x1b", "cut short"),
    ("[ 0%] Checking for available updates...", "[ 0%] Checking for available updates..."),
])
def test_strip_ansi(raw, shown):
    assert strip_ansi(raw) == shown


async def test_a_job_log_line_loses_its_terminal_colours():
    """SteamCMD colours its output; the browser's log showed the escape codes
    as text (`[0m@ShutdownOnFailedCommand`). What a page fetches and what the
    WebSocket sends are both the plain line."""
    hub = WsHub()
    sent = []

    async def capture(message):
        sent.append(message)

    hub.broadcast = capture
    job = Job(id="abc", name="test", kind="test", hub=hub)
    job.loop = asyncio.get_running_loop()

    job.log_line("\x1b[0m@ShutdownOnFailedCommand")
    await asyncio.sleep(0)

    assert job.logs_after(0) == [(1, "@ShutdownOnFailedCommand")]
    assert [m["line"] for m in sent] == ["@ShutdownOnFailedCommand"]


async def test_a_subprocess_line_is_redacted_and_loses_its_colours():
    """The transform (the Steam login's password redaction) still runs on the
    line as SteamCMD wrote it."""
    import sys

    from app.web.jobs import stream_subprocess

    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()
    script = "print('\\x1b[1mLogging in user hunter2\\x1b[0m')"

    returncode = await stream_subprocess(
        job, [sys.executable, "-c", script], transform=lambda line: line.replace("hunter2", "***"),
    )

    assert returncode == 0
    assert [line for _, line in job.logs_after(0)] == ["Logging in user ***"]


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


async def test_job_log_line_appends_and_broadcasts():
    hub = WsHub()
    job = Job(id="abc", name="test", kind="test", hub=hub)
    job.loop = asyncio.get_running_loop()

    job.log_line("hello")
    job.log_line("world")
    await asyncio.sleep(0)  # let the fire-and-forget broadcast tasks run

    assert job.logs_after(0) == [(1, "hello"), (2, "world")]
    assert job.logs_after(1) == [(2, "world")]


async def test_job_log_line_threadsafe_from_worker_thread():
    hub = WsHub()
    job = Job(id="abc", name="test", kind="test", hub=hub)
    job.loop = asyncio.get_running_loop()

    await asyncio.to_thread(job.log_line_threadsafe, "from a thread")
    await asyncio.sleep(0.05)  # call_soon_threadsafe needs a loop tick

    assert [line for _, line in job.logs_after(0)] == ["from a thread"]


def test_job_to_dict_shape():
    job = Job(id="abc", name="test job", kind="update", hub=WsHub())
    data = job.to_dict()
    assert data["id"] == "abc"
    assert data["name"] == "test job"
    assert data["kind"] == "update"
    assert data["status"] == "pending"
    assert data["error"] is None
    assert data["result"] is None


# ---------------------------------------------------------------------------
# JobManager
# ---------------------------------------------------------------------------


async def _wait_until_done(job: Job, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while job.status in ("pending", "running"):
        if time.monotonic() > deadline:
            raise AssertionError(f"job did not finish in time (status={job.status})")
        await asyncio.sleep(0.01)


async def test_job_manager_runs_job_to_success():
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> dict:
        job.log_line("working")
        return {"ok": True}

    job = manager.start("Test", "test", _coro)
    await _wait_until_done(job)

    assert job.status == "success"
    assert job.result == {"ok": True}
    assert job.started_at is not None
    assert job.finished_at is not None


async def test_job_manager_marks_failure():
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> dict:
        raise RuntimeError("boom")

    job = manager.start("Test", "test", _coro)
    await _wait_until_done(job)

    assert job.status == "failed"
    assert job.error == "boom"
    # The log is what the pages show, so the reason ends up there too.
    assert job.log[-1][1] == "boom"


async def test_a_failure_the_job_already_logged_is_not_logged_twice():
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> dict:
        job.log_line("Error: boom")
        raise RuntimeError("boom")

    job = manager.start("Test", "test", _coro)
    await _wait_until_done(job)

    assert [line for _, line in job.log] == ["Error: boom"]


async def test_job_manager_fires_on_finished_after_releasing_busy():
    """WebNotifier re-runs its doctor check from this hook, so `busy` must
    already read False by the time it fires."""
    manager = JobManager(WsHub())
    seen: list[tuple[str, bool]] = []
    manager.on_finished.append(lambda job: seen.append((job.status, manager.busy)))

    job = manager.start("Test", "test", lambda j: _noop())
    await _wait_until_done(job)

    assert seen == [("success", False)]


async def test_job_manager_on_finished_fires_for_failed_job():
    manager = JobManager(WsHub())
    seen: list[str] = []

    def _boom(job: Job) -> None:
        raise RuntimeError("callback exploded")

    manager.on_finished.append(_boom)  # a raising hook must not break the job
    manager.on_finished.append(lambda job: seen.append(job.status))

    async def _fail(job: Job) -> dict:
        raise RuntimeError("boom")

    job = manager.start("Test", "test", _fail)
    await _wait_until_done(job)

    assert job.status == "failed"
    assert seen == ["failed"]


async def _noop() -> None:
    return None


async def test_job_manager_exclusive_lock_conflicts():
    manager = JobManager(WsHub())
    gate = asyncio.Event()

    async def _slow(job: Job) -> dict | None:
        await gate.wait()
        return None

    first = manager.start("First", "test", _slow)
    await asyncio.sleep(0.01)  # let it acquire the lock

    with pytest.raises(JobConflictError) as exc_info:
        manager.start("Second", "test", _slow)
    assert exc_info.value.active_job_id == first.id

    gate.set()
    await _wait_until_done(first)


async def test_job_manager_non_exclusive_jobs_run_concurrently():
    manager = JobManager(WsHub())
    gate = asyncio.Event()

    async def _slow(job: Job) -> dict | None:
        await gate.wait()
        return None

    async def _fast(job: Job) -> dict:
        return {"done": True}

    first = manager.start("First", "test", _slow, exclusive=False)
    second = manager.start("Second", "test", _fast, exclusive=False)
    await _wait_until_done(second)

    assert second.status == "success"
    assert first.status == "running"
    gate.set()
    await _wait_until_done(first)


async def test_job_manager_cancel_sets_event_and_status():
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> dict | None:
        await job.cancel_event.wait()
        return None

    job = manager.start("Test", "test", _coro)
    await asyncio.sleep(0.01)
    assert manager.cancel(job.id) is True
    await _wait_until_done(job)

    assert job.status == "cancelled"


def test_job_manager_cancel_unknown_job_returns_false():
    manager = JobManager(WsHub())
    assert manager.cancel("does-not-exist") is False


async def test_job_manager_exception_after_cancel_reads_as_cancelled():
    """A step that fails because it was cancelled (a download that returned
    False) must not turn the cancel into a "failed" job."""
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> None:
        await job.cancel_event.wait()
        raise RuntimeError("Failed to download @Mod")

    job = manager.start("Test", "test", _coro)
    await asyncio.sleep(0.01)
    manager.cancel(job.id)
    await _wait_until_done(job)

    assert job.status == "cancelled"


async def test_job_manager_kill_stops_a_job_that_ignores_its_cancel():
    manager = JobManager(WsHub())

    async def _stuck(job: Job) -> None:
        await asyncio.sleep(60)  # never looks at cancel_event

    job = manager.start("Stuck", "test", _stuck)
    await asyncio.sleep(0.05)
    manager.cancel(job.id)
    await asyncio.sleep(0.05)
    assert job.status == "running" and manager.busy

    assert manager.kill(job.id) is True
    await _wait_until_done(job)

    assert job.status == "cancelled"
    assert job.to_dict()["kill_requested"] is True
    assert manager.busy is False  # the exclusive slot is free for the next job


async def test_job_manager_kill_refuses_unknown_and_finished_jobs():
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> None:
        return None

    job = manager.start("Done", "test", _coro)
    await _wait_until_done(job)

    assert manager.kill(job.id) is False
    assert manager.kill("does-not-exist") is False
    assert job.kill_requested is False


async def test_job_manager_keeps_last_50_jobs():
    manager = JobManager(WsHub())

    async def _coro(job: Job) -> None:
        return None

    jobs = []
    for _ in range(55):
        job = manager.start("Test", "test", _coro, exclusive=False)
        jobs.append(job)
        await _wait_until_done(job)

    assert len(manager.jobs) == 50
    assert jobs[0].id not in manager.jobs
    assert jobs[-1].id in manager.jobs


# ---------------------------------------------------------------------------
# stream_subprocess / JobRunner
# ---------------------------------------------------------------------------


async def test_stream_subprocess_captures_output():
    from app.web.jobs import stream_subprocess

    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()

    returncode = await stream_subprocess(job, ["echo", "hello from subprocess"])

    assert returncode == 0
    lines = [line for _, line in job.logs_after(0)]
    assert any("hello from subprocess" in line for line in lines)


async def test_a_quiet_steamcmd_job_says_it_is_still_working(monkeypatch):
    """SteamCMD block-buffers its pipe, so a download can sit under its banner
    for minutes; the job log says it is still working, outside the tail."""
    import sys
    from app.shared import steam
    from app.web.jobs import _stream

    monkeypatch.setattr(steam, "_QUIET_SECONDS", 0.5)
    monkeypatch.setattr(steam, "_QUIET_POLL_SECONDS", 0.1)
    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()
    tail = []
    quiet = [sys.executable, "-c", "import time; print('-- type quit --', flush=True); time.sleep(1.5)"]

    returncode = await _stream(job, quiet, None, lambda line: tail.append(line) or line, say_when_quiet=True)

    lines = [line for _, line in job.logs_after(0)]
    assert returncode == 0
    assert any(line.startswith("Zedstead: SteamCMD is still working (") for line in lines)
    assert tail == ["-- type quit --"]


async def test_stream_subprocess_cancel_terminates_process():
    from app.web.jobs import stream_subprocess

    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()

    task = asyncio.create_task(stream_subprocess(job, ["sleep", "30"]))
    await asyncio.sleep(0.1)
    job.cancel_event.set()

    returncode = await asyncio.wait_for(task, timeout=5)
    assert returncode != 0


def _pid_gone(pid: int) -> bool:
    """True once *pid* no longer exists or is only a zombie awaiting its reaper."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            return fh.read().rsplit(b")", 1)[1].split()[0] == b"Z"
    except OSError:
        return True


async def _wait_pid_gone(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while not _pid_gone(pid):
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX process tree via /proc")
async def test_stream_subprocess_cancel_reaches_a_wrapper_scripts_child(tmp_path):
    """steamcmd.sh runs the real SteamCMD as a child. Terminating only the
    wrapper orphaned that child, which held stdout open, so a Cancel waited for
    the whole download (measured: 14.7s of a 15s sleep)."""
    from app.web.jobs import stream_subprocess

    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()
    pid_file = tmp_path / "child.pid"

    task = asyncio.create_task(
        stream_subprocess(job, ["bash", "-c", f"sleep 30 & echo $! > {pid_file}; wait"])
    )
    while not pid_file.exists() or not pid_file.read_text().strip():
        await asyncio.sleep(0.02)
    child = int(pid_file.read_text())

    started = time.monotonic()
    job.cancel_event.set()
    await asyncio.wait_for(task, timeout=5)

    assert time.monotonic() - started < 5
    assert await _wait_pid_gone(child)


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX process tree via /proc")
async def test_job_manager_kill_kills_the_subprocess_tree(tmp_path):
    from app.web.jobs import stream_subprocess

    manager = JobManager(WsHub())
    pid_file = tmp_path / "child.pid"
    # SIGTERM is trapped, so a plain cancel can't end it before its grace.
    script = f"trap '' TERM; sleep 30 & echo $! > {pid_file}; wait; wait"

    async def _coro(job: Job) -> None:
        await stream_subprocess(job, ["bash", "-c", script])

    job = manager.start("SteamCMD", "test", _coro)
    while not pid_file.exists() or not pid_file.read_text().strip():
        await asyncio.sleep(0.02)
    child = int(pid_file.read_text())

    manager.cancel(job.id)
    assert manager.kill(job.id) is True
    await _wait_until_done(job, timeout=5)

    assert job.status == "cancelled"
    assert await _wait_pid_gone(child)


async def test_job_runner_exposes_cancelled():
    job = Job(id="x", name="x", kind="x", hub=WsHub())
    runner = JobRunner(job)
    assert runner.cancelled is False
    job.cancel_event.set()
    assert runner.cancelled is True


async def test_job_runner_write_strips_markup():
    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()
    runner = JobRunner(job)

    runner.write("[green]all good[/green]")

    assert [line for _, line in job.logs_after(0)] == ["all good"]


async def test_job_runner_run_command_delegates_to_stream_subprocess():
    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()
    runner = JobRunner(job)

    returncode = await runner.run_command(["true"])

    assert returncode == 0


async def test_job_runner_keeps_what_the_command_printed():
    """steam.runner_no_subscription_error reads this to say why SteamCMD
    failed; the job log still gets every line."""
    import sys

    job = Job(id="x", name="x", kind="x", hub=WsHub())
    job.loop = asyncio.get_running_loop()
    runner = JobRunner(job)
    line = "ERROR! Failed to install app '223350' (No subscription)"

    returncode = await runner.run_command(
        [sys.executable, "-c", f"import sys; print({line!r}); sys.exit(8)"]
    )

    assert returncode == 8
    assert runner.last_output == line
    assert [text for _, text in job.logs_after(0)] == [line]


# ---------------------------------------------------------------------------
# HTTP layer: /api/jobs, /api/update/*, /api/logs/rpt
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_and_config(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    return app


def _login(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200


def _poll_job(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} did not finish in time: {body}")
        time.sleep(0.02)


def _mock_pipeline_ops(
    monkeypatch, *, remove_old_mods_delay: float = 0.0, entered: threading.Event | None = None
) -> None:
    """No-op every real side effect the update pipeline would otherwise
    trigger (SteamCMD, filesystem mod ops, XML merge, systemd, Discord).
    *entered* is set when the job reaches the slow step."""
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["true"])
    # plans/73's snapshot reads the workshop manifest, which sample_config does
    # not sandbox.
    from app.ops import post_update

    monkeypatch.setattr(post_update, "snapshot", lambda config: post_update.UpdateSnapshot(1.0, ""))
    monkeypatch.setattr("app.modmgr.modlist.installed_and_ids", lambda config: ([], {}))

    def _remove_old_mods(config, log=None):
        if entered is not None:
            entered.set()
        if remove_old_mods_delay:
            time.sleep(remove_old_mods_delay)

    monkeypatch.setattr("app.ops.fileops.remove_server_mods", _remove_old_mods)
    monkeypatch.setattr("app.ops.fileops.copy_all_mods_to_server", lambda config, log=None: 0)
    monkeypatch.setattr("app.ops.fileops.copy_bikey_files", lambda config, log=None: None)
    monkeypatch.setattr(
        "app.modmgr.mods.get_current_mission", lambda cfg_path: "dayzOffline.chernarusplus"
    )
    monkeypatch.setattr("app.modmgr.mods.require_mission", lambda config: "dayzOffline.chernarusplus")
    monkeypatch.setattr(
        "app.xml.xml_economy.merge_economy_core",
        lambda server_dir, mission, *, manual_dir, disabled_mods=None, log=None: None,
    )
    monkeypatch.setattr(
        "app.commands.run._merge_all_xml_configs",
        lambda config, mission, disabled, log=None: [],
    )

    async def _fake_generate(runner, config, **kwargs):
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_generate)
    monkeypatch.setattr("app.ops.changeset_ops.take_snapshot", lambda config: None)
    monkeypatch.setattr(
        "app.monitoring.discord_hooks.notify_update", lambda config, summary="": None
    )


def test_jobs_list_requires_session(app_and_config):
    resp = TestClient(app_and_config).get("/api/jobs")
    assert resp.status_code == 401


def test_get_unknown_job_404(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_cancel_unknown_job_404(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        assert client.post("/api/jobs/does-not-exist/cancel").status_code == 404


def test_update_steps_shape(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.get("/api/update/steps")

    assert resp.status_code == 200
    body = resp.json()
    # steam_ok is no longer part of this payload — the slow SteamCMD login probe
    # moved to GET /api/steam/status so steps render instantly. See UpdatePage.
    assert "steam_ok" not in body
    keys = [s["key"] for s in body["steps"]]
    assert keys == [
        "update_server", "download_mods", "remove_old_mods",
        "copy_mods", "copy_bikeys", "merge_xml", "finalize",
    ]
    undoable = {s["key"] for s in body["steps"] if s["undoable"]}
    assert undoable == {"remove_old_mods", "copy_mods", "merge_xml"}
    # The Update page greys these out while Steam is logged out.
    steam = {s["key"] for s in body["steps"] if s["needs_steam"]}
    assert steam == {"update_server", "download_mods"}


def test_update_run_rejects_while_server_running(app_and_config, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/run", json={})
    assert resp.status_code == 409
    # The Guide's Install button shows this detail verbatim (plans/42 F4).
    assert "force" not in resp.json()["detail"]
    assert "stop it" in resp.json()["detail"]


def test_a_dry_run_goes_ahead_while_the_server_runs(app_and_config, monkeypatch):
    """A dry run writes nothing, so it is not refused for a running server:
    the Update page's Dry run answered "stop it first" (night test 2026-11-22)."""
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/run", json={"dry_run": True})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
    assert body["status"] == "success"


def test_update_run_force_overrides_running_check(app_and_config, monkeypatch):
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    _mock_pipeline_ops(monkeypatch)
    # is_server_running is patched True above; _mock_pipeline_ops overwrites
    # it back to False, so re-apply True last to actually test `force`.
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/run", json={"force": True})
        assert resp.status_code == 200
        # Leave the block only once the job is done: closing the client while
        # the job is inside create_subprocess_exec hung the test for good.
        _poll_job(client, resp.json()["job_id"])


def test_update_run_unknown_step_422(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/run", json={"steps": ["not_a_real_step"]})
    assert resp.status_code == 422


def test_update_run_end_to_end_with_noop_steps(app_and_config, monkeypatch):
    """The plan's explicit test: monkeypatch every underlying op to a no-op,
    run the real pipeline through the job engine, poll to success."""
    _mock_pipeline_ops(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/run", json={"dry_run": False})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        body = _poll_job(client, job_id)

    assert body["status"] == "success", body
    statuses = {r["key"]: r["status"] for r in body["result"]["results"]}
    assert all(s == "success" for s in statuses.values()), statuses


def test_update_run_with_a_failed_step_ends_failed_and_keeps_its_results(app_and_config, monkeypatch):
    """It ended "success", so the toast said "Install DayZ server finished" over
    a failed install (browser test 2026-11-17, F6). The Update page still reads
    the step results off the failed job."""
    _mock_pipeline_ops(monkeypatch)

    def _boom(config, log=None):
        raise OSError("disk full")

    monkeypatch.setattr("app.ops.fileops.copy_bikey_files", _boom)

    with TestClient(app_and_config) as client:
        _login(client)
        job_id = client.post("/api/update/run", json={}).json()["job_id"]
        body = _poll_job(client, job_id)

    assert body["status"] == "failed", body
    assert "disk full" in body["error"]
    statuses = {r["key"]: r["status"] for r in body["result"]["results"]}
    assert statuses["copy_bikeys"] == "failed"
    assert statuses["update_server"] == "success"


def test_update_run_dry_run_does_not_touch_anything(app_and_config, monkeypatch):
    """No mocks needed at all — dry-run must never call the real ops; if it
    did, this would fail on a missing SteamCMD binary etc."""
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/run", json={"dry_run": True})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success"
    statuses = {r["key"] for r in body["result"]["results"] if r["status"] == "dry-run"}
    assert len(statuses) == 7


def test_jobs_list_and_get_reflect_update_job(app_and_config, monkeypatch):
    _mock_pipeline_ops(monkeypatch)
    with TestClient(app_and_config) as client:
        _login(client)
        job_id = client.post("/api/update/run", json={}).json()["job_id"]

        listed = client.get("/api/jobs").json()
        assert any(j["id"] == job_id for j in listed)

        body = _poll_job(client, job_id)
    assert body["status"] == "success"


def test_job_get_includes_log_with_seq_and_line(app_and_config, monkeypatch):
    _mock_pipeline_ops(monkeypatch)
    with TestClient(app_and_config) as client:
        _login(client)
        job_id = client.post("/api/update/run", json={}).json()["job_id"]
        body = _poll_job(client, job_id)
        assert body["status"] == "success"

        log = client.get(f"/api/jobs/{job_id}?log_after=0").json()["log"]
        assert isinstance(log, list)
        if log:
            assert set(log[0].keys()) == {"seq", "line"}

        # A seq beyond the end of the log returns nothing further.
        caught_up = client.get(f"/api/jobs/{job_id}?log_after=999999").json()["log"]
        assert caught_up == []


def test_job_cancel_via_http(app_and_config, monkeypatch):
    _mock_pipeline_ops(monkeypatch, remove_old_mods_delay=0.5)
    with TestClient(app_and_config) as client:
        _login(client)
        job_id = client.post("/api/update/run", json={}).json()["job_id"]

        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200

        body = _poll_job(client, job_id, timeout=5)
    assert body["status"] == "cancelled"


def test_job_cancel_sets_cancel_requested_before_the_job_stops(app_and_config, monkeypatch):
    """The UI disables its Cancel button off cancel_requested — the flag has to
    be visible while the job is still winding down, not only once it ended."""
    _mock_pipeline_ops(monkeypatch, remove_old_mods_delay=0.5)
    with TestClient(app_and_config) as client:
        _login(client)
        job_id = client.post("/api/update/run", json={}).json()["job_id"]
        assert client.get(f"/api/jobs/{job_id}").json()["cancel_requested"] is False

        client.post(f"/api/jobs/{job_id}/cancel")
        pending = client.get(f"/api/jobs/{job_id}").json()
        assert pending["cancel_requested"] is True

        body = _poll_job(client, job_id, timeout=5)
    assert body["status"] == "cancelled"
    assert body["cancel_requested"] is True


def test_job_kill_via_http(app_and_config, monkeypatch):
    # Wait for the slow step, not just "running": a cancel that lands in the
    # update step stops its subprocess at once, and the kill then finds the
    # job finished (404).
    entered = threading.Event()
    _mock_pipeline_ops(monkeypatch, remove_old_mods_delay=0.5, entered=entered)
    with TestClient(app_and_config) as client:
        _login(client)
        job_id = client.post("/api/update/run", json={}).json()["job_id"]
        assert entered.wait(3)

        client.post(f"/api/jobs/{job_id}/cancel")
        assert client.post(f"/api/jobs/{job_id}/kill").status_code == 200

        body = _poll_job(client, job_id, timeout=5)
        assert body["status"] == "cancelled"
        assert body["kill_requested"] is True
        lines = [e["line"] for e in client.get(f"/api/jobs/{job_id}").json()["log"]]
        assert any(line.startswith("Killed") for line in lines)

        # Finished now — a second kill is a 404, like a late cancel.
        assert client.post(f"/api/jobs/{job_id}/kill").status_code == 404


def test_kill_unknown_job_404(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        assert client.post("/api/jobs/does-not-exist/kill").status_code == 404


def test_update_run_conflict_returns_409_with_active_job_id(app_and_config, monkeypatch):
    _mock_pipeline_ops(monkeypatch, remove_old_mods_delay=0.5)
    with TestClient(app_and_config) as client:
        _login(client)
        first_id = client.post("/api/update/run", json={}).json()["job_id"]

        resp = client.post("/api/update/run", json={})
        assert resp.status_code == 409
        assert resp.json()["active_job_id"] == first_id

        client.post(f"/api/jobs/{first_id}/cancel")
        _poll_job(client, first_id, timeout=5)


def test_update_rollback_runs_undo_for_requested_steps(app_and_config, monkeypatch):
    undo_calls = []
    monkeypatch.setattr(
        "app.ops.fileops.remove_server_mods",
        # `stash` since TODO C5: the undo must NOT set the mods it is removing
        # aside, or it would replace the good previous version with the broken
        # one. tests/test_mod_rollback.py asserts the value.
        lambda config, log=None, stash=True: undo_calls.append("remove_server_mods"),
    )
    # The web's fresh context removes no mod: it only puts back what the
    # stash holds (BUG-016), so give it one. tests/test_mod_rollback.py holds
    # the mods on disk.
    from app.ops import mod_rollback

    monkeypatch.setattr(mod_rollback, "rollback_state", lambda config: {"available": True})
    monkeypatch.setattr(
        mod_rollback, "restore",
        lambda config, log=None: undo_calls.append("restore")
        or mod_rollback.RestoreResult(ok=True, restored=["@Mod"], message="Restored."),
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/rollback", json={"steps": ["copy_mods"]})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success"
    assert undo_calls == ["restore"]



def test_update_rollback_job_fails_when_an_undo_fails(app_and_config, monkeypatch):
    """An undo that raised can leave the server with no mods, so the job is
    "failed" with the step's name, never "success"."""
    from app.ops import mod_rollback

    monkeypatch.setattr(mod_rollback, "rollback_state", lambda config: {"available": True})
    monkeypatch.setattr(
        mod_rollback, "restore",
        lambda config, log=None: mod_rollback.RestoreResult(ok=False, restored=[], message="disk full"),
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/rollback", json={"steps": ["copy_mods"]})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "failed"
    assert "Rollback failed for: " in body["error"]

def test_update_rollback_ignores_non_undoable_step(app_and_config):
    """copy_bikeys has no undo — requesting it rolls back nothing (not an
    error), matching build_update_steps' undoable set."""
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/rollback", json={"steps": ["copy_bikeys"]})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])
    assert body["status"] == "success"


def test_update_rollback_unknown_step_422(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/update/rollback", json={"steps": ["not_a_real_step"]})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/logs/rpt
# ---------------------------------------------------------------------------


def test_rpt_snapshot_reads_fixture_file(app_and_config, sample_config):
    profiles_dir = sample_config.server_dir_path / sample_config.profiles_path
    profiles_dir.mkdir(parents=True, exist_ok=True)
    rpt_path = profiles_dir / "DayZServer_x64_2026-01-01_12-00-00.RPT"
    rpt_path.write_text("line one\nline two\nline three\n", encoding="utf-8")

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.get("/api/logs/rpt?lines=2")

    assert resp.status_code == 200
    assert resp.json()["lines"] == ["line two", "line three"]


def test_rpt_snapshot_flags_the_lines_every_stock_boot_prints(app_and_config, sample_config):
    """Night test 2026-11-22: Key lines showed a vanilla server's stock
    engine lines as red errors. The snapshot flags them so the pane mutes them."""
    profiles_dir = sample_config.server_dir_path / sample_config.profiles_path
    profiles_dir.mkdir(parents=True, exist_ok=True)
    rpt_path = profiles_dir / "DayZServer_x64_2026-01-01_12-00-00.RPT"
    rpt_path.write_text(
        " 9:00:01 ENTITY    (E): Type 'InventedType' must be inherited from class 'OtherType'\n"
        " 9:00:02 SCRIPT    (E): Invented mod error\n",
        encoding="utf-8",
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.get("/api/logs/rpt")

    assert resp.json()["stock"] == [True, False]


def test_rpt_snapshot_names_the_zone_of_its_times(app_and_config, monkeypatch):
    """Night test 2026-11-22: the drawer showed the server log's UTC times
    beside browser times an hour apart. The offset lets the browser convert."""
    import time

    monkeypatch.setenv("TZ", "Etc/GMT-2")  # POSIX sign: two hours east of UTC
    time.tzset()
    try:
        with TestClient(app_and_config) as client:
            _login(client)
            resp = client.get("/api/logs/rpt")
    finally:
        monkeypatch.undo()
        time.tzset()
    assert resp.json()["utc_offset_minutes"] == 120


def test_rpt_snapshot_empty_when_no_rpt_files(app_and_config):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.get("/api/logs/rpt")
    assert resp.status_code == 200
    assert resp.json()["lines"] == []


def test_strip_markup_keeps_brackets_that_are_not_rich_tags():
    """User test in 2026 Q4: the update log lost its "[1/3]" counters, so
    each mod's copy read as two lines for the same thing."""
    from app.web.jobs import strip_markup

    assert strip_markup("  [1/3] Copying [cyan]@CF[/cyan]...") == "  [1/3] Copying @CF..."
    assert strip_markup("[green]✓ Copied 3/3 mods.[/green]") == "✓ Copied 3/3 mods."
    assert strip_markup("[  0%] Checking for available updates...") == "[  0%] Checking for available updates..."
    assert strip_markup("user 'x' [U:1:58] to Steam") == "user 'x' [U:1:58] to Steam"
    assert strip_markup("a \\[dim] b") == "a [dim] b"
