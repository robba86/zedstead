"""zsm updating itself: app/ops/self_update.py, `zsm self-update` and
GET|POST /api/self-update. git and uv are faked at self_update's own
bindings; nothing here runs either for real."""

from __future__ import annotations

import time

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from app.ops import self_update

PASSWORD = "testpass123"


class FakeGit:
    """Answers the git calls self_update makes, and records them."""

    def __init__(self, *, head="aaa1111", remote="bbb2222", behind=2, dirty="", fail=None):
        self.head, self.remote, self.behind, self.dirty, self.fail = head, remote, behind, dirty, fail
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, directory, *args, timeout=0):
        self.calls.append(args)
        if self.fail and args[0] == self.fail:
            raise self_update.SelfUpdateError(f"git {args[0]} failed: no route to host")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return "main"
        if args[:2] == ("rev-parse", "--short"):
            return self.head if args[2] == "HEAD" else self.remote
        if args[0] == "rev-list":
            return str(self.behind)
        if args[0] == "log":
            return "\n".join(f"change {i}" for i in range(self.behind))
        if args[0] == "status":
            return self.dirty
        if args[0] == "reset":
            self.head = self.remote
        return ""

    def ran(self, name: str) -> bool:
        return any(c[0] == name for c in self.calls)


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    src = tmp_path / "zsm"
    (src / ".git").mkdir(parents=True)
    monkeypatch.setattr(self_update, "source_dir", lambda: src)
    monkeypatch.setattr(self_update, "IS_DOCKER", False)
    monkeypatch.setattr(self_update, "IS_WINDOWS", False)
    monkeypatch.setattr(self_update, "_uv", lambda: "/usr/bin/uv")
    return src


@pytest.fixture()
def git(checkout, monkeypatch):
    fake = FakeGit()
    monkeypatch.setattr(self_update, "_git", fake)
    return fake


@pytest.fixture()
def uv(monkeypatch):
    runs: list[list[str]] = []

    def _stream(argv, log, timeout):
        runs.append(argv)
        log("Installed 1 executable: zsm")

    monkeypatch.setattr(self_update, "_stream", _stream)
    return runs


# ── check ────────────────────────────────────────────────────────────────


def test_check_counts_the_new_commits_on_the_install_branch(git):
    info = self_update.check()
    assert info.supported and not info.error
    assert (info.branch, info.current, info.latest, info.behind) == ("main", "aaa1111", "bbb2222", 2)
    assert info.subjects == ["change 0", "change 1"]
    assert ("fetch", "origin", "main") in git.calls


def test_check_can_skip_the_fetch(git):
    self_update.check(fetch=False)
    assert not git.ran("fetch")


def test_check_reports_a_failed_fetch_instead_of_raising(checkout, monkeypatch):
    monkeypatch.setattr(self_update, "_git", FakeGit(fail="fetch"))
    info = self_update.check()
    assert info.supported
    assert "no route to host" in info.error


def test_docker_is_told_to_rebuild_the_image(checkout, git, monkeypatch):
    monkeypatch.setattr(self_update, "IS_DOCKER", True)
    info = self_update.check()
    assert (info.supported, info.reason) == (False, "docker")
    assert "docker compose build" in info.command
    assert git.calls == []


def test_an_install_without_a_checkout_is_told_to_run_the_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(self_update, "source_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(self_update, "IS_DOCKER", False)
    info = self_update.check()
    assert (info.supported, info.reason) == (False, "no_checkout")
    assert "install.sh" in info.command


def test_windows_still_checks_but_offers_the_installer(git, monkeypatch):
    monkeypatch.setattr(self_update, "IS_WINDOWS", True)
    info = self_update.check()
    assert (info.supported, info.reason, info.behind) == (False, "windows", 2)
    assert "install.ps1" in info.command


def test_credentials_in_a_remote_url_are_hidden():
    assert self_update.redact("fatal: https://bob:tok3n@github.com/x") == "fatal: https://***@github.com/x"


# ── apply ────────────────────────────────────────────────────────────────


def test_apply_resets_to_the_remote_and_reinstalls(checkout, git, uv):
    lines: list[str] = []
    result = self_update.apply(lines.append)
    assert result == {"before": "aaa1111", "after": "bbb2222", "changed": True}
    assert ("reset", "--hard", "origin/main") in git.calls
    assert uv == [["/usr/bin/uv", "tool", "install", "--force", "--reinstall", str(checkout)]]
    assert "Installed 1 executable: zsm" in lines


def test_apply_with_nothing_new_reinstalls_nothing(checkout, monkeypatch, uv):
    fake = FakeGit(remote="aaa1111", behind=0)
    monkeypatch.setattr(self_update, "_git", fake)
    assert self_update.apply(lambda _l: None)["changed"] is False
    assert not fake.ran("reset")
    assert uv == []


def test_apply_refuses_a_checkout_with_local_changes(checkout, monkeypatch, uv):
    fake = FakeGit(dirty=" M app/__init__.py")
    monkeypatch.setattr(self_update, "_git", fake)
    with pytest.raises(self_update.SelfUpdateError, match="local changes"):
        self_update.apply(lambda _l: None)
    assert not fake.ran("fetch") and not fake.ran("reset")
    assert uv == []


@pytest.mark.parametrize("flag,reason", [("IS_DOCKER", "docker"), ("IS_WINDOWS", "windows")])
def test_apply_refuses_where_it_cannot_update(checkout, git, uv, monkeypatch, flag, reason):
    monkeypatch.setattr(self_update, flag, True)
    with pytest.raises(self_update.SelfUpdateError, match=self_update.HOW[reason][1].split()[0]):
        self_update.apply(lambda _l: None)
    assert git.calls == [] and uv == []


def test_apply_refuses_outside_a_checkout(tmp_path, monkeypatch, uv):
    monkeypatch.setattr(self_update, "source_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(self_update, "IS_DOCKER", False)
    monkeypatch.setattr(self_update, "IS_WINDOWS", False)
    with pytest.raises(self_update.SelfUpdateError, match="installer"):
        self_update.apply(lambda _l: None)


# ── the CLI ──────────────────────────────────────────────────────────────


def _cli(*args):
    from app.commands.self_update import self_update_cmd

    return CliRunner().invoke(self_update_cmd, list(args), obj={"non_interactive": True})


def test_cli_check_lists_the_changes_and_installs_nothing(git, uv):
    result = _cli("--check")
    assert result.exit_code == 0, result.output
    assert "2 new change(s) on main" in result.output and "change 1" in result.output
    assert uv == []


def test_cli_updates_and_names_the_panel_restart(git, uv, monkeypatch):
    from app.commands import self_update as cmd

    monkeypatch.setattr(cmd, "_panel_restart_hint", lambda: "systemctl --user restart zsm-web")
    result = _cli()
    assert result.exit_code == 0, result.output
    assert uv and "systemctl --user restart zsm-web" in result.output


def test_cli_exits_non_zero_where_it_cannot_update(checkout, monkeypatch):
    monkeypatch.setattr(self_update, "IS_DOCKER", True)
    result = _cli()
    assert result.exit_code == 1
    assert "docker compose build" in result.output


# ── the web ──────────────────────────────────────────────────────────────


def _app(config):
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(config)


def _wait(client, job_id) -> dict:
    deadline = time.monotonic() + 3.0
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.02)


def test_only_an_admin_may_check_or_update():
    from app.web.permissions import ADMIN, SIDE_EFFECTING_READS, required_role

    assert required_role("GET", "/api/self-update") == ADMIN
    assert required_role("POST", "/api/self-update") == ADMIN
    assert "/api/self-update" in SIDE_EFFECTING_READS


def test_get_answers_the_check_with_the_running_version(sample_config, git):
    from app import __version__

    with TestClient(_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        body = client.get("/api/self-update").json()
    assert body["version"] == __version__
    assert body["behind"] == 2 and body["supported"] is True


def test_the_job_updates_and_asks_the_panel_to_restart(sample_config, git, uv, monkeypatch):
    from app.web.routers import self_update as route

    monkeypatch.setattr(route, "RESTART_DELAY", 0.0)
    restarts: list[bool] = []
    app = _app(sample_config)
    app.state.request_restart = lambda: restarts.append(True)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        job = _wait(client, client.post("/api/self-update").json()["job_id"])
        deadline = time.monotonic() + 2.0
        while not restarts and time.monotonic() < deadline:
            time.sleep(0.02)
    assert job["status"] == "success", job
    assert job["result"]["restarting"] is True
    assert restarts == [True]


def test_without_a_restart_hook_the_job_says_to_restart_by_hand(sample_config, git, uv):
    with TestClient(_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        job_id = client.post("/api/self-update").json()["job_id"]
        job = _wait(client, job_id)
    assert job["status"] == "success", job
    assert job["result"]["restarting"] is False


def test_a_refused_update_fails_the_job_and_restarts_nothing(sample_config, checkout, uv, monkeypatch):
    monkeypatch.setattr(self_update, "_git", FakeGit(dirty=" M x"))
    restarts: list[bool] = []
    app = _app(sample_config)
    app.state.request_restart = lambda: restarts.append(True)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        job = _wait(client, client.post("/api/self-update").json()["job_id"])
    assert job["status"] == "failed"
    assert "local changes" in job["error"]
    assert restarts == [] and uv == []


def test_the_update_waits_for_no_other_job(sample_config, git, uv):
    """It is an exclusive job: beside a running job it answers 409."""
    import asyncio

    app = _app(sample_config)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        gate = asyncio.Event()

        async def _hold(job):
            await gate.wait()

        state = app.state.panel.startup
        held = client.portal.call(lambda: _start(state, _hold))
        assert client.post("/api/self-update").status_code == 409
        client.portal.call(gate.set)
        _wait(client, held)


async def _start(state, coro):
    return state.jobs.start("Hold", "hold", coro).id


def test_start_server_returns_whether_a_restart_was_asked(sample_config, monkeypatch):
    """The hook stops uvicorn and makes start_server answer True."""
    import asyncio

    import uvicorn

    from app.web import server

    async def _serve(self):
        self.config.app.state.request_restart()
        assert self.should_exit is True

    monkeypatch.setattr(uvicorn.Server, "serve", _serve)
    assert asyncio.run(server.start_server(sample_config)) is True


def test_web_execs_itself_again_after_a_restart(monkeypatch):
    from app.commands import web

    calls = []
    monkeypatch.setattr(web.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    monkeypatch.setattr(web.sys, "argv", ["/x/zsm", "--instance", "pvp", "web", "--port", "9000"])
    web._restart_self()
    exe, argv = calls[0]
    assert argv == [exe, "-m", "app", "--instance", "pvp", "web", "--port", "9000"]
