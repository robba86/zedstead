"""Tests for GET /api/setup-state (app/web/routers/setup.py).

Nothing here may run `systemctl` or SteamCMD. Both are patched in every test
that reaches them — `service_setup_state` shells out, and the Steam answer is
supposed to come from the cached probe rather than a fresh login check, which
is the single most important claim this file pins.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import write_test_mods

PASSWORD = "testpass123"

STEP_IDS = ("steam", "install", "maps", "mods", "presets", "service", "start", "backup", "scheduler")


@pytest.fixture()
def no_subprocesses(monkeypatch):
    """Answer the two shelling-out probes in-process, for every test here."""
    from app.server import service_control

    monkeypatch.setattr(
        service_control, "service_setup_state", lambda config: {"installed": False, "enabled_at_boot": False}
    )
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.shared.steam.check_steam_login", lambda config, timeout=15, **k: False)


@pytest.fixture()
def client(sample_config, no_subprocesses):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_it_requires_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/setup-state").status_code == 401


def test_every_step_id_is_answered_with_done_and_detail(client):
    body = client.get("/api/setup-state").json()
    assert set(body["steps"]) == set(STEP_IDS)
    for step_id, answer in body["steps"].items():
        assert isinstance(answer["done"], bool), step_id
        assert isinstance(answer["detail"], str), step_id


def test_steam_comes_from_the_cached_answer_never_a_steamcmd_run(
    sample_config, monkeypatch, no_subprocesses
):
    """The rule this endpoint exists under: two SteamCMD processes on one
    STEAMROOT corrupt the cached login token, so a read-only endpoint polled by
    an always-mounted drawer must never start one. Since plans/51 D it does
    not even go through `steam_probe` (which still ran SteamCMD whenever its
    30 s cache had expired): it reads `WebState.steam_known`."""
    from app.web import server
    from app.web.auth import hash_password
    from app.web.state import WebState

    async def no_probe(self, *, force=False) -> bool:
        pytest.fail("/api/setup-state must not probe")

    monkeypatch.setattr(WebState, "steam_probe", no_probe)
    monkeypatch.setattr(
        "app.shared.steam.check_steam_login",
        lambda *a, **k: pytest.fail("check_steam_login must not be called from /api/setup-state"),
    )

    sample_config.web_password_hash = hash_password(PASSWORD)
    sample_config.steam_username = "bob"
    sample_config.steamcmd_path.parent.mkdir(parents=True, exist_ok=True)
    sample_config.steamcmd_path.write_text("#!/bin/sh\nexit 0\n")
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    state = tclient.app.state.ctx

    step = tclient.get("/api/setup-state").json()["steps"]["steam"]
    assert step == {"done": False, "detail": "Steam login not checked yet."}

    state.note_steam_ok(True)
    step = tclient.get("/api/setup-state").json()["steps"]["steam"]
    assert step == {"done": True, "detail": "Logged in to Steam."}

    state.note_steam_ok(False)
    step = tclient.get("/api/setup-state").json()["steps"]["steam"]
    assert step == {"done": False, "detail": "Not logged in to Steam."}


def test_install_is_false_without_the_binary_and_true_with_it(client, sample_config):
    from app.shared.platform import IS_WINDOWS

    assert client.get("/api/setup-state").json()["steps"]["install"]["done"] is False

    binary = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"
    (sample_config.server_dir_path / binary).write_text("", encoding="utf-8")

    answer = client.get("/api/setup-state").json()["steps"]["install"]
    assert answer["done"] is True
    assert answer["detail"]


def test_service_and_start_are_given_the_loaded_config(
    sample_config, monkeypatch, no_subprocesses
):
    """Hot-path rule: pass the caller's config so `get_scope` doesn't re-read
    config.json per poll. Asserting the argument is the only way to keep it."""
    from app.server import service_control
    from app.web import server
    from app.web.auth import hash_password

    seen = {}

    def fake_setup_state(config):
        seen["service"] = config
        return {"installed": True, "enabled_at_boot": True}

    def fake_running(config=None):
        seen["start"] = config
        return False

    monkeypatch.setattr(service_control, "service_setup_state", fake_setup_state)
    monkeypatch.setattr(service_control, "is_server_running", fake_running)

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    body = tclient.get("/api/setup-state").json()

    assert seen["service"] is sample_config
    assert seen["start"] is sample_config
    assert body["steps"]["service"]["done"] is True
    assert body["steps"]["service"]["detail"] == "Service installed, starts at boot."
    assert body["steps"]["start"]["done"] is False
    assert body["steps"]["start"]["detail"] == "Server is stopped."


def test_presets_has_no_detector_and_says_so_by_answering_nothing(client):
    """Deliberate: there is no honest filesystem fact behind "a preset was
    applied", so the hand tick owns this step."""
    answer = client.get("/api/setup-state").json()["steps"]["presets"]
    assert answer == {"done": False, "detail": ""}


def test_mods_counts_mods_txt_and_says_it_in_words(client, sample_config):
    assert client.get("/api/setup-state").json()["steps"]["mods"] == {"done": False, "detail": ""}

    write_test_mods(sample_config, ['@CF'])
    assert client.get("/api/setup-state").json()["steps"]["mods"] == {
        "done": True,
        "detail": "1 mod installed",
    }

    write_test_mods(sample_config, ['@CF', '@DayZExpansion'])
    assert client.get("/api/setup-state").json()["steps"]["mods"]["detail"] == "2 mods installed"


def test_maps_reports_the_active_map_name(client, monkeypatch):
    import app.maps.map_ops as map_ops

    monkeypatch.setattr(map_ops, "get_active_map_name", lambda config: "Livonia")
    answer = client.get("/api/setup-state").json()["steps"]["maps"]
    assert answer == {"done": True, "detail": "Livonia"}

    monkeypatch.setattr(map_ops, "get_active_map_name", lambda config: None)
    assert client.get("/api/setup-state").json()["steps"]["maps"]["done"] is False


def test_the_stock_chernarus_is_not_a_picked_map(client, monkeypatch):
    """A fresh install already runs Chernarus; that is not the admin's pick."""
    import app.maps.map_ops as map_ops

    monkeypatch.setattr(map_ops, "get_active_map_name", lambda config: "Chernarus")
    answer = client.get("/api/setup-state").json()["steps"]["maps"]
    assert answer["done"] is False
    assert answer["level"] == "warning"
    assert answer["detail"].startswith("Your server runs Chernarus, the map DayZ installs by default.")


def test_backup_and_scheduler_count_what_their_pages_list(client, monkeypatch):
    """A backup or a scheduled task the admin made marks its Guide step done."""
    import app.ops.backup_ops as backup_ops
    import app.server.scheduler as scheduler

    monkeypatch.setattr(backup_ops, "list_backups", lambda config, mission=None: [])
    monkeypatch.setattr(scheduler, "load_tasks", lambda config: [])
    steps = client.get("/api/setup-state").json()["steps"]
    assert steps["backup"] == {"done": False, "detail": ""}
    assert steps["scheduler"] == {"done": False, "detail": ""}

    monkeypatch.setattr(backup_ops, "list_backups", lambda config, mission=None: ["a", "b"])
    monkeypatch.setattr(scheduler, "load_tasks", lambda config: ["t"])
    steps = client.get("/api/setup-state").json()["steps"]
    assert steps["backup"] == {"done": True, "detail": "2 quick backups"}
    assert steps["scheduler"] == {"done": True, "detail": "1 scheduled task"}
