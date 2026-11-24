"""Tests for the Stage 9 web endpoints: /api/service/{setup,regenerate,remove,boot}
(app/web/routers/service.py) and /api/steam/* (app/web/routers/steam.py)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server.service_control import ServiceResult
from app.shared.platform import IS_WINDOWS

PASSWORD = "testpass123"


@pytest.fixture()
def web_app(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.save()
    return server.create_app(sample_config)


@pytest.fixture()
def client(web_app):
    tclient = TestClient(web_app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def _poll_job(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} did not finish in time: {body}")
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# /api/service/setup
# ---------------------------------------------------------------------------


def test_service_setup_requires_session(web_app):
    assert TestClient(web_app).get("/api/service/setup").status_code == 401


def test_service_setup_state_shape(client, monkeypatch):
    monkeypatch.setattr(
        "app.server.service_control.service_setup_state",
        lambda config: {"installed": True, "enabled_at_boot": False},
    )
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )
    resp = client.get("/api/service/setup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is True
    assert body["enabled_at_boot"] is False
    assert body["running"] is False
    for key in ("has_systemd", "is_windows", "sudo_available"):
        assert key in body


# ---------------------------------------------------------------------------
# /api/service/regenerate
# ---------------------------------------------------------------------------


def test_regenerate_stops_running_server_and_generates(client, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: True
    )
    monkeypatch.setattr(
        "app.server.service_control.stop_server",
        lambda config: calls.append("stop") or ServiceResult(True),
    )
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files",
        lambda config: calls.append("generate"),
    )
    # Bypass the sudo gate regardless of the test host's systemd state.
    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: False)

    resp = client.post("/api/service/regenerate")
    assert resp.status_code == 200
    assert calls == ["stop", "generate"]


def test_regenerate_bad_unit_user_returns_409(client, monkeypatch):
    """An unresolvable User= is a config problem, so it must surface as a 409.

    Without explicit handling this raises out of the route as an unhandled
    ValueError (500) and the operator never sees the actual cause.
    """
    from app.server.service_control import ServiceUserError

    def _boom(config):
        raise ServiceUserError("The service would run as user 'dayz' ... 217/USER")

    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )
    monkeypatch.setattr("app.server.service_ops.generate_service_files", _boom)
    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: False)

    resp = client.post("/api/service/regenerate")
    assert resp.status_code == 409
    assert "217/USER" in resp.json()["detail"]


def test_regenerate_sudo_gate(client, monkeypatch):
    monkeypatch.setattr("app.web.routers.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: True)
    # system scope (pinned by conftest) → gate probes sudo_available_noninteractive
    monkeypatch.setattr(
        "app.shared.systemd_scope.sudo_available_noninteractive", lambda: False
    )
    resp = client.post("/api/service/regenerate")
    assert resp.status_code == 409
    assert "sudo" in resp.json()["detail"]


def test_regenerate_user_scope_bus_down_409(sample_config, monkeypatch):
    """User scope with an unreachable user bus → 409 with the linger command."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.service_scope = "user"
    sample_config.save()

    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: True)
    monkeypatch.setattr("app.web.routers.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.systemd_scope.user_bus_available", lambda: False)
    monkeypatch.setattr("app.shared.systemd_scope._current_user", lambda: "bob")

    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    resp = tclient.post("/api/service/regenerate")
    assert resp.status_code == 409
    assert "enable-linger bob" in resp.json()["detail"]


def test_service_info_user_scope_fields(sample_config, monkeypatch):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.service_scope = "user"
    sample_config.save()

    monkeypatch.setattr("app.shared.systemd_scope.user_bus_available", lambda: True)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: True)

    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    body = tclient.get("/api/service/info").json()
    assert body["scope"] == "user"
    assert body["privileged_ops_ok"] is True
    assert body["linger_enabled"] is True


# ---------------------------------------------------------------------------
# /api/service/remove + /api/service/boot
# ---------------------------------------------------------------------------


def test_remove_service_ok(client, monkeypatch):
    monkeypatch.setattr("app.web.routers.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: False)
    monkeypatch.setattr(
        "app.server.service_control.remove_service", lambda config=None: ServiceResult(True)
    )
    assert client.post("/api/service/remove").status_code == 200


def test_remove_service_failure_409(client, monkeypatch):
    monkeypatch.setattr("app.web.routers.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: False)
    monkeypatch.setattr(
        "app.server.service_control.remove_service",
        lambda config=None: ServiceResult(False, "Service is not installed."),
    )
    resp = client.post("/api/service/remove")
    assert resp.status_code == 409
    assert "not installed" in resp.json()["detail"]


def test_boot_toggle(client, monkeypatch):
    seen: list[bool] = []
    monkeypatch.setattr("app.web.routers.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.web.routers.service.has_systemd", lambda: False)
    monkeypatch.setattr(
        "app.server.service_control.set_boot_enabled",
        lambda enabled, config=None: seen.append(enabled) or ServiceResult(True),
    )
    resp = client.post("/api/service/boot", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled_at_boot"] is True
    resp = client.post("/api/service/boot", json={"enabled": False})
    assert resp.status_code == 200
    assert seen == [True, False]


# ---------------------------------------------------------------------------
# /api/steam/status
# ---------------------------------------------------------------------------


def test_steam_status_no_steamcmd(client):
    resp = client.get("/api/steam/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["steamcmd_present"] is False
    # Known without measuring: there is nothing to log in with (plans/51 D).
    assert body["logged_in"] is False
    assert client.get("/api/steam/status?check=1").json()["logged_in"] is False


def test_steam_status_is_null_until_measured(client, sample_config, monkeypatch):
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login", lambda *a, **k: pytest.fail("a read ran SteamCMD")
    )
    state = client.app.state.ctx
    state.config.steam_username = "bob"
    _install_fake_steamcmd(state.config)
    assert client.get("/api/steam/status").json()["logged_in"] is None


# ---------------------------------------------------------------------------
# /api/steam/login
# ---------------------------------------------------------------------------


def test_steam_login_requires_steamcmd(client):
    resp = client.post(
        "/api/steam/login", json={"username": "bob", "password": "hunter2"}
    )
    assert resp.status_code == 409


def _install_fake_steamcmd(sample_config) -> None:
    path = sample_config.steamcmd_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")


def _script_of(cmd: list[str]) -> Path:
    """The login script a SteamCMD argv runs."""
    assert cmd[1] == "+runscript", cmd
    return Path(cmd[2])


def test_steam_login_job_redacts_password(web_app, sample_config, monkeypatch):
    _install_fake_steamcmd(sample_config)
    seen_cmd: list[str] = []
    seen_script: list[str] = []

    async def fake_stream(job, cmd, cwd=None, transform=None):
        seen_cmd.extend(cmd)
        seen_script.append(_script_of(cmd).read_text(encoding="utf-8"))
        for line in (
            "Logging in user 'bob' to Steam Public...",
            "password echo: hunter2 guard: GUARD42",
            "OK",
        ):
            job.log_line(transform(line) if transform else line)
        return 0

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/login",
            json={"username": "bob", "password": "hunter2", "guard_code": "GUARD42"},
        )
        assert resp.status_code == 200
        job = _poll_job(client, resp.json()["job_id"])
        log_after = client.get(f"/api/jobs/{job['id']}?log_after=0").json()

    assert job["status"] == "success", job
    assert job["result"] == {"logged_in": True, "username": "bob"}
    # Secrets reach SteamCMD through its script, never its command line...
    assert not [part for part in seen_cmd if "hunter2" in part or "GUARD42" in part]
    assert 'login "bob" "hunter2" "GUARD42"' in seen_script[0]
    # ...and never the job log.
    log_text = "\n".join(entry["line"] for entry in log_after["log"])
    assert "hunter2" not in log_text
    assert "GUARD42" not in log_text
    assert "***" in log_text


def test_steam_login_failure_marks_job_failed(web_app, sample_config, monkeypatch):
    _install_fake_steamcmd(sample_config)

    async def fake_stream(job, cmd, cwd=None, transform=None):
        job.log_line("FAILED (Invalid Password)")
        return 5

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/login", json={"username": "bob", "password": "wrong"}
        )
        job = _poll_job(client, resp.json()["job_id"])

    assert job["status"] == "failed"
    assert "Steam login failed" in job["error"]


def test_the_login_job_name_does_not_carry_the_account(web_app, sample_config, monkeypatch):
    """Every role sees job names, a viewer included (SEC-7); the account name
    is half of a Steam login."""
    _install_fake_steamcmd(sample_config)

    async def fake_stream(job, cmd, cwd=None, transform=None):
        return 0

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/steam/login", json={"username": "bobsaccount", "password": "pw"})
        job = _poll_job(client, resp.json()["job_id"])

    assert job["name"] == "Steam login"


def test_steam_login_persists_username(web_app, sample_config, monkeypatch):
    _install_fake_steamcmd(sample_config)

    async def fake_stream(job, cmd, cwd=None, transform=None):
        return 0

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/login", json={"username": "newuser", "password": "pw"}
        )
        job = _poll_job(client, resp.json()["job_id"])
        assert job["status"] == "success"
        status = client.get("/api/steam/status").json()

    assert status["username"] == "newuser"


# ---------------------------------------------------------------------------
# /api/steam/request-guard (phase 1 of the two-pass login)
# ---------------------------------------------------------------------------


def test_request_guard_requires_steamcmd(client):
    resp = client.post(
        "/api/steam/request-guard", json={"username": "bob", "password": "hunter2"}
    )
    assert resp.status_code == 409


def _fake_steamcmd_run(monkeypatch, returncode: int, output: str = ""):
    def fake_run(cmd, **kwargs):
        import types

        return types.SimpleNamespace(
            returncode=returncode, stdout=output.encode(), stderr=b""
        )

    monkeypatch.setattr("app.shared.steamcmd_lock.run_to_end", fake_run)


def test_request_guard_needs_code_on_failure(web_app, sample_config, monkeypatch):
    _install_fake_steamcmd(sample_config)
    # Unclassifiable failure — still ask for a code (the pre-diagnosis default).
    _fake_steamcmd_run(monkeypatch, 5)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/request-guard",
            json={"username": "bob", "password": "hunter2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "logged_in": False,
            "needs_code": True,
            "username": "bob",
            "factor": None,
        }
        # Codeless failure must NOT persist the username.
        status = client.get("/api/steam/status").json()

    assert status["username"] != "bob"


@pytest.mark.parametrize(
    "output,factor",
    [
        ("Steam Guard code:", "email"),
        ("FAILED (Account Logon Denied)", "email"),
        ("Two-factor code:", "mobile"),
        ("FAILED (Account Login Denied Need Two Factor)", "mobile"),
    ],
)
def test_request_guard_reports_which_factor(
    web_app, sample_config, monkeypatch, output, factor
):
    """The client wording depends on this: a mobile-authenticator account is
    never emailed a code, so it must not be told to check its inbox."""
    _install_fake_steamcmd(sample_config)
    _fake_steamcmd_run(monkeypatch, 5, output)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/request-guard",
            json={"username": "bob", "password": "hunter2"},
        )

    assert resp.status_code == 200
    assert resp.json()["factor"] == factor
    assert resp.json()["needs_code"] is True


@pytest.mark.parametrize(
    "output,needle",
    [
        ("FAILED login with result code Invalid Password", "password"),
        ("FAILED (Rate Limit Exceeded)", "rate-limiting"),
    ],
)
def test_request_guard_rejects_hard_failures(
    web_app, sample_config, monkeypatch, output, needle
):
    """No code was sent, so prompting for one is a dead end — 400 instead."""
    _install_fake_steamcmd(sample_config)
    _fake_steamcmd_run(monkeypatch, 5, output)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/request-guard",
            json={"username": "bob", "password": "hunter2"},
        )
        assert resp.status_code == 400
        assert needle in resp.json()["detail"]
        status = client.get("/api/steam/status").json()

    assert status["username"] != "bob"


def test_request_guard_output_never_leaks_the_password(
    web_app, sample_config, monkeypatch
):
    _install_fake_steamcmd(sample_config)
    _fake_steamcmd_run(monkeypatch, 5, "FAILED (Invalid Password) hunter2")

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/request-guard",
            json={"username": "bob", "password": "hunter2"},
        )

    assert "hunter2" not in resp.text


def test_steam_login_job_error_names_the_real_reason(
    web_app, sample_config, monkeypatch
):
    _install_fake_steamcmd(sample_config)

    async def fake_stream(job, cmd, cwd=None, transform=None):
        job.log_line(transform("FAILED (Two Factor Code Mismatch)"))
        return 5

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/login",
            json={"username": "bob", "password": "pw", "guard_code": "ABCDE"},
        )
        job = _poll_job(client, resp.json()["job_id"])

    assert job["status"] == "failed"
    assert "rejected" in job["error"]


def test_request_guard_logs_in_when_no_guard(web_app, sample_config, monkeypatch):
    _install_fake_steamcmd(sample_config)
    _fake_steamcmd_run(monkeypatch, 0)  # no Guard / already cached

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/request-guard",
            json={"username": "nogaurd", "password": "pw"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "logged_in": True,
            "needs_code": False,
            "username": "nogaurd",
        }
        status = client.get("/api/steam/status").json()

    assert status["username"] == "nogaurd"
    assert status["logged_in"] is True  # believed, not re-measured


@pytest.mark.parametrize("route", ["/api/steam/request-guard", "/api/steam/login"])
def test_anonymous_is_refused_before_steamcmd(web_app, sample_config, monkeypatch, route):
    """An anonymous login succeeds and then owns nothing: SteamCMD refuses
    the DayZ server with "No subscription" (measured on a VPS, 2026 Q3)."""
    from app.shared.steam import ANONYMOUS_USERNAME_ERROR

    import types

    _install_fake_steamcmd(sample_config)
    spawned: list = []  # logins as anonymous; the startup probe runs as testuser

    def fake_run(cmd, **kwargs):
        if "+runscript" in cmd:  # a login; the startup probe is `+login testuser`
            spawned.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("app.shared.steamcmd_lock.run_to_end", fake_run)

    async def fake_stream(job, cmd, cwd=None, transform=None):
        spawned.append(cmd)
        return 0

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(route, json={"username": " Anonymous ", "password": "x"})
        status = client.get("/api/steam/status").json()

    assert resp.status_code == 400
    assert resp.json()["detail"] == ANONYMOUS_USERNAME_ERROR
    assert spawned == []
    assert status["username"] == "testuser"


@pytest.mark.parametrize(
    "route,body,field",
    [
        ("/api/steam/request-guard", {"username": "+quit", "password": "x"}, "username"),
        ("/api/steam/request-guard", {"username": "two words", "password": "x"}, "username"),
        ("/api/steam/login", {"username": "-bob", "password": "x"}, "username"),
        ("/api/steam/login", {"username": "bob\nx", "password": "x"}, "username"),
        ("/api/steam/login", {"username": "bob", "password": "x", "guard_code": "+quit"}, "Guard code"),
        ("/api/steam/login", {"username": "bob", "password": "x", "guard_code": "AB 12"}, "Guard code"),
        # Cannot be written inside the login script's quotes (SEC-3).
        ("/api/steam/request-guard", {"username": "bob", "password": 'pa"ss'}, "password"),
        ("/api/steam/login", {"username": "bob", "password": "pa\nquit"}, "password"),
    ],
)
def test_a_value_steamcmd_would_misread_is_refused_before_steamcmd(
    web_app, sample_config, monkeypatch, route, body, field
):
    # Each goes to SteamCMD as one argument after +login; one starting with +
    # would be read as its next command.
    import types

    _install_fake_steamcmd(sample_config)
    spawned: list = []

    def fake_run(cmd, **kwargs):
        if "+runscript" in cmd:  # a login; the startup probe is `+login testuser`
            spawned.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("app.shared.steamcmd_lock.run_to_end", fake_run)

    async def fake_stream(job, cmd, cwd=None, transform=None):
        spawned.append(cmd)
        return 0

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(route, json=body)
        status = client.get("/api/steam/status").json()

    assert resp.status_code == 400
    assert field in resp.json()["detail"]
    assert body["password"] not in resp.text
    assert spawned == []
    assert status["username"] == "testuser"


# ---------------------------------------------------------------------------
# The password and Guard code never reach SteamCMD's command line (SEC-3)
# ---------------------------------------------------------------------------


def _mode(path: Path) -> int:
    import stat

    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("outcome", ["exit", "timeout"])
def test_the_guard_request_hands_steamcmd_a_private_script(
    web_app, sample_config, monkeypatch, outcome
):
    """argv is world-readable in /proc/<pid>/cmdline; the script is not, and it
    is gone once SteamCMD is, a timeout included."""
    import subprocess
    import types

    _install_fake_steamcmd(sample_config)
    seen: list[dict] = []

    def fake_run(cmd, **kwargs):
        if "+runscript" not in cmd:
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        script = _script_of(cmd)
        seen.append({
            "cmd": cmd,
            "script": script,
            "text": script.read_text(encoding="utf-8"),
            "modes": None if IS_WINDOWS else (_mode(script.parent), _mode(script)),
        })
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, 120, output=b"Steam Guard code:")
        return types.SimpleNamespace(returncode=5, stdout=b"Steam Guard code:", stderr=b"")

    monkeypatch.setattr("app.shared.steamcmd_lock.run_to_end", fake_run)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/request-guard", json={"username": "bob", "password": "hun ter;2//x"}
        )

    assert resp.status_code == 200
    assert resp.json()["factor"] == "email"  # the Guard prompt is still read
    [run] = seen
    assert not [part for part in run["cmd"] if "hun ter" in part]
    assert run["text"] == '@ShutdownOnFailedCommand 1\nlogin "bob" "hun ter;2//x"\nquit\n'
    if run["modes"] is not None:
        assert run["modes"] == (0o700, 0o600)
    assert not run["script"].parent.exists()


def test_a_finished_login_job_leaves_no_script(web_app, sample_config, monkeypatch):
    _install_fake_steamcmd(sample_config)
    scripts: list[Path] = []

    async def fake_stream(job, cmd, cwd=None, transform=None):
        scripts.append(_script_of(cmd))
        assert scripts[0].is_file()
        if not IS_WINDOWS:
            assert (_mode(scripts[0].parent), _mode(scripts[0])) == (0o700, 0o600)
        return 5

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post("/api/steam/login", json={"username": "bob", "password": "pw"})
        job = _poll_job(client, resp.json()["job_id"])

    assert job["status"] == "failed"
    assert scripts and not scripts[0].parent.exists()


def test_a_killed_login_job_leaves_no_script(web_app, sample_config, monkeypatch):
    """Kill cancels the job's task while SteamCMD runs; the script goes too."""
    import asyncio

    _install_fake_steamcmd(sample_config)
    scripts: list[Path] = []

    async def fake_stream(job, cmd, cwd=None, transform=None):
        scripts.append(_script_of(cmd))
        await asyncio.sleep(30)  # SteamCMD sitting at a prompt
        return 0

    monkeypatch.setattr("app.web.routers.steam.stream_subprocess", fake_stream)

    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        resp = client.post(
            "/api/steam/login", json={"username": "bob", "password": "pw", "guard_code": "ABCDE"}
        )
        job_id = resp.json()["job_id"]
        deadline = time.monotonic() + 3
        while not scripts and time.monotonic() < deadline:
            time.sleep(0.02)
        assert scripts and scripts[0].is_file()
        client.post(f"/api/jobs/{job_id}/cancel")
        assert client.post(f"/api/jobs/{job_id}/kill").status_code == 200
        job = _poll_job(client, job_id)

    assert job["status"] in ("cancelled", "failed")
    assert not scripts[0].parent.exists()


def test_a_login_in_the_panel_is_announced_to_every_page(web_app, sample_config, monkeypatch):
    """plans/51 D: pages follow `steam_changed` instead of probing, so the
    Dashboard card and the Mods banner must hear about a login made here."""
    _install_fake_steamcmd(sample_config)
    _fake_steamcmd_run(monkeypatch, 0)
    sent: list[dict] = []
    real = web_app.state.ctx.hub.broadcast

    async def record(message, channel=None):
        sent.append(message)
        await real(message, channel)

    monkeypatch.setattr(web_app.state.ctx.hub, "broadcast", record)
    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        client.post("/api/steam/request-guard", json={"username": "bob", "password": "pw"})

    assert {"type": "steam_changed", "logged_in": True} in sent


# ---------------------------------------------------------------------------
# Steam probe must never run next to a job
# ---------------------------------------------------------------------------


def test_steam_status_does_not_probe_while_a_job_runs(client, sample_config, monkeypatch):
    """Two SteamCMD processes on one STEAMROOT corrupt the cached login token
    and the workshop manifest — even an explicit check must not spawn one
    beside a download job. Serving the last known answer also stops the login
    card from flashing back mid-install."""
    calls: list[str] = []
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda config, *a, **kw: calls.append("probe") or True,
    )
    state = client.app.state.ctx
    state.config.steam_username = "bob"
    _install_fake_steamcmd(state.config)

    assert client.get("/api/steam/status?check=1").json()["logged_in"] is True
    assert len(calls) == 1

    state.jobs._active_exclusive = object()  # a job is pending/running
    try:
        assert client.get("/api/steam/status?check=1").json()["logged_in"] is True
        assert client.get("/api/steam/status").json()["logged_in"] is True
        assert client.get("/api/status").json()["steam_ok"] is True
    finally:
        state.jobs._active_exclusive = None

    assert len(calls) == 1  # cached, not re-probed


def test_plain_reads_never_run_steamcmd(client, monkeypatch):
    """plans/51 D (N3): /api/status probed whenever its 30 s cache had expired,
    so a Dashboard reload ran `steamcmd +login` inside the request (10.4 s
    measured) while the page had already filled its strip from the WebSocket."""
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda *a, **k: pytest.fail("a read ran SteamCMD"),
    )
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )
    for path in ("/api/status", "/api/steam/status", "/api/setup-state"):
        assert client.get(path).status_code == 200


def test_check_again_measures_even_inside_the_ttl_and_tells_every_page(client, monkeypatch):
    answers = iter([False, True])
    calls: list[str] = []
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda config, *a, **kw: calls.append("probe") or next(answers),
    )
    sent: list[dict] = []

    async def record(message, channel=None):
        sent.append(message)

    state = client.app.state.ctx
    monkeypatch.setattr(state.hub, "broadcast", record)

    assert client.get("/api/steam/status?check=1").json()["logged_in"] is False
    # Logged in from a shell a moment later: "Check again" must see it.
    assert client.get("/api/steam/status?check=1").json()["logged_in"] is True
    assert len(calls) == 2
    assert {"type": "steam_changed", "logged_in": True} in sent


async def test_readers_racing_each_other_spawn_exactly_one_steamcmd(
    sample_config, monkeypatch
):
    """`jobs.busy` guards the probe against a *job*, and nothing else.

    Two read-only callers racing is easy to arrange — two browser tabs
    hydrating, or /api/status and /api/setup-state both refreshing on a single
    `service_changed` broadcast — and each would have spawned its own SteamCMD
    on the same STEAMROOT: the corruption the busy guard exists to prevent.
    """
    import asyncio

    from app.web.state import WebState

    calls: list[str] = []

    def slow_probe(config, *a, **kw):
        calls.append("probe")
        time.sleep(0.05)  # long enough for the others to pile up on the lock
        return True

    monkeypatch.setattr("app.shared.steam.check_steam_login", slow_probe)
    state = WebState(sample_config)

    answers = await asyncio.gather(*(state.steam_probe() for _ in range(4)))
    assert answers == [True, True, True, True]
    assert len(calls) == 1


async def test_a_login_made_through_the_app_is_believed_without_a_second_probe(
    sample_config, monkeypatch
):
    """The stale-cache trap: a cached `False` outliving the login the user just
    completed would leave the login card on screen after a success."""
    from app.web.state import WebState

    calls: list[str] = []
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda config, *a, **kw: calls.append("probe") or False,
    )
    state = WebState(sample_config)
    assert await state.steam_probe() is False

    state.note_steam_ok(True)  # what both login routes do on success
    assert await state.steam_probe() is True
    assert len(calls) == 1


async def test_an_answer_older_than_the_ttl_is_measured_again(sample_config, monkeypatch):
    """The cache is a burst collapser, not a permanent answer — a token
    deleted outside the app still has to show up."""
    from app.web import state as state_mod

    calls: list[str] = []
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda config, *a, **kw: calls.append("probe") or True,
    )
    state = state_mod.WebState(sample_config)
    assert await state.steam_probe() is True

    state.steam_checked_at -= state_mod.STEAM_PROBE_TTL + 1
    assert await state.steam_probe() is True
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# plans/51 D: the login is re-measured after a job that ran SteamCMD, at
# startup, and a job waits out a probe already in flight
# ---------------------------------------------------------------------------


async def _settle() -> None:
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_job_that_ran_steamcmd_is_followed_by_one_recheck(sample_config, monkeypatch):
    from app.web.state import WebState

    calls: list[str] = []
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda config, *a, **kw: calls.append("probe") or True,
    )
    sample_config.steam_username = "bob"
    _install_fake_steamcmd(sample_config)
    state = WebState(sample_config)

    async def ran_steamcmd(job):
        job.ran_steamcmd = True  # what stream_subprocess sets for a steamcmd argv

    async def did_not(job):
        return None

    for coro, kind in ((did_not, "backup_create"), (ran_steamcmd, "update"), (ran_steamcmd, "steam_login")):
        job = state.jobs.start("t", kind, coro)
        await job.task
        await _settle()
    # Only the update counted: a job without SteamCMD has nothing to say, and
    # the login route records its own answer.
    assert calls == ["probe"]
    assert state.steam_known() is True


async def test_stream_subprocess_marks_a_steamcmd_run(sample_config, tmp_path):
    import sys

    from app.web.jobs import Job, stream_subprocess
    from app.web.ws import WsHub

    steamcmd = tmp_path / "steamcmd.sh"
    steamcmd.write_text("#!/bin/sh\nexit 0\n")
    steamcmd.chmod(0o755)

    job = Job(id="a", name="t", kind="update", hub=WsHub())
    await stream_subprocess(job, [sys.executable, "-c", "print(1)"])
    assert job.ran_steamcmd is False
    await stream_subprocess(job, [str(steamcmd)])
    assert job.ran_steamcmd is True


async def test_an_exclusive_job_waits_for_a_probe_in_flight(sample_config):
    """`busy` keeps a probe from starting beside a job; this is the other
    direction — finish a mod install, click Update while the re-check runs."""
    import asyncio

    from app.web.state import WebState

    state = WebState(sample_config)
    started = asyncio.Event()

    async def body(job):
        started.set()

    await state._steam_lock.acquire()  # a probe is running SteamCMD
    job = state.jobs.start("t", "update", body)
    await _settle()
    assert not started.is_set()
    state._steam_lock.release()
    await job.task
    assert started.is_set()


def test_startup_measures_once_when_there_is_a_login_to_measure(sample_config, monkeypatch):
    from app.web import server

    calls: list[str] = []
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda config, *a, **kw: calls.append("probe") or True,
    )
    sample_config.steam_username = "bob"
    _install_fake_steamcmd(sample_config)

    app = server.create_app(sample_config)
    with TestClient(app):
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)
    assert calls == ["probe"]
    assert app.state.ctx.steam_known() is True


def test_startup_does_not_measure_without_a_username(sample_config, monkeypatch):
    from app.web import server

    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda *a, **k: pytest.fail("probed with nothing to log in as"),
    )
    sample_config.steam_username = ""
    _install_fake_steamcmd(sample_config)
    with TestClient(server.create_app(sample_config)):
        time.sleep(0.05)



@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_service_start_stop_restart_refused_while_a_job_runs(client, monkeypatch, action):
    """An update, restore or reinstall job starts and stops the server itself,
    so a Start beside it would boot a half-written server dir."""
    from app.web.jobs import Job

    called: list[str] = []
    ok = ServiceResult(True, "ok")
    for fn in ("start_server", "stop_server", "restart_server"):
        monkeypatch.setattr(
            f"app.server.service_control.{fn}",
            lambda config=None, *a, _fn=fn, **k: called.append(_fn) or ok,
        )
    monkeypatch.setattr("app.server.service_control.start_refusal", lambda config=None: None)

    jobs = client.app.state.ctx.jobs
    jobs._active_exclusive = Job(id="busyjob", name="n", kind="k", hub=jobs.hub)
    try:
        resp = client.post(f"/api/service/{action}")
    finally:
        jobs._active_exclusive = None
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Another job is already running: busyjob"
    assert called == []
