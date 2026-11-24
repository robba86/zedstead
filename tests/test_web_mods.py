"""Tests for /api/mods* (app/web/routers/mods.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.modmgr import modlist
from tests.conftest import disabled_names, is_manual, learned_ids, manual_dir, pinned_names, server_only_names, set_instance_id, set_test_flags, write_test_ids, write_test_mods

PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def app_and_config(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


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


def test_mods_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/mods")
    assert resp.status_code == 401


def test_list_mods_empty(client):
    resp = client.get("/api/mods")
    assert resp.status_code == 200
    assert resp.json() == {"mods": [], "spawn_test": None}


def test_list_mods_shape_reflects_disabled_and_installed(client, sample_config):
    write_test_mods(sample_config, ['@CF', '@TraderPlus'])
    set_test_flags(sample_config, disabled=["@TraderPlus"])
    sample_config.save()
    (sample_config.server_dir_path / "@CF").mkdir()

    resp = client.get("/api/mods")
    assert resp.status_code == 200
    by_name = {m["name"]: m for m in resp.json()["mods"]}
    assert by_name["@CF"]["installed"] is True
    assert by_name["@CF"]["enabled"] is True
    assert by_name["@TraderPlus"]["installed"] is False
    assert by_name["@TraderPlus"]["enabled"] is False


def test_search_mods(client, monkeypatch):
    monkeypatch.setattr(
        "app.shared.steam.search_workshop", lambda query: [("123", "Community Framework")]
    )
    resp = client.get("/api/mods/search", params={"q": "community"})
    assert resp.status_code == 200
    assert resp.json() == {"results": [{"id": "123", "title": "Community Framework"}]}


def test_add_mod(client, monkeypatch):
    # A name zsm does not ship, so the Steam lookup is what answers — @CF now
    # resolves from the built-in registry without a request at all
    # (tests/test_mod_ops.py::test_resolve_mod_workshop_id_prefers_the_registry_over_the_network).
    monkeypatch.setattr("app.shared.steam.resolve_mod_id", lambda name, cached: "555")
    resp = client.post("/api/mods", json={"name": "NotInAnyRegistry"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] is True
    assert body["name"] == "@NotInAnyRegistry"
    assert body["id"] == "555"


def test_disable_mod_unmerges_mission_xml(client, sample_config, tmp_mission_dir):
    """Toggling a mod off must strip its direct-merge blocks too — the
    cfgeconomycore half alone left spawn positions live in the mission."""
    from tests.conftest import write_xml

    write_test_mods(sample_config, ['@Bicycles'])
    sample_config.save()
    target = write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Vanilla"><pos x="1" z="2" a="0"/></event></eventposdef>',
    )
    write_xml(
        sample_config.server_dir_path / "@Bicycles" / "cfgeventspawns_Chernarus.xml",
        '<?xml version="1.0"?><events><event name="Bike"><pos x="5" y="6" z="7" a="0"/></event></events>',
    )

    assert client.post("/api/mods/@Bicycles/enable").status_code == 200
    assert "Bike" in target.read_text(encoding="utf-8")

    assert client.post("/api/mods/@Bicycles/disable").status_code == 200
    text = target.read_text(encoding="utf-8")
    assert "Bike" not in text
    assert "Vanilla" in text


def test_enable_disable_mod_round_trip(client, sample_config, monkeypatch):
    write_test_mods(sample_config, ['@CF'])
    sample_config.save()

    economy_calls = []
    monkeypatch.setattr(
        "app.xml.xml_economy.disable_mod_economy_entries",
        lambda server_dir, mission, name: economy_calls.append(("disable", name)),
    )
    monkeypatch.setattr(
        "app.xml.xml_economy.enable_mod_economy_entries",
        lambda server_dir, mission, name: economy_calls.append(("enable", name)),
    )
    monkeypatch.setattr(
        "app.modmgr.mods.get_current_mission", lambda cfg_path: "dayzOffline.chernarusplus"
    )

    regen_calls = []

    async def _fake_service(runner, config, **kw):
        regen_calls.append(True)
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)

    resp = client.post("/api/mods/@CF/disable")
    assert resp.status_code == 200
    from app.shared.config import DayzConfig

    assert "@CF" in disabled_names(sample_config)

    resp = client.post("/api/mods/@CF/enable")
    assert resp.status_code == 200
    assert "@CF" not in disabled_names(sample_config)

    # A toggle flips the CE entries and regenerates the service.
    assert economy_calls == [("disable", "@CF"), ("enable", "@CF")]
    assert len(regen_calls) == 2


def test_server_side_toggle_round_trip(client, sample_config, monkeypatch):
    # plans/47 A: the list reports the mark, the route flips it and rebuilds
    # the unit, which is where -serverMod= lives.
    write_test_mods(sample_config, ['@CF', '@DayZ-Dynamic-AI-Addon'])
    sample_config.save()
    regen_calls = []

    async def _fake_service(runner, config, **kw):
        regen_calls.append(list(server_only_names(config)))
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)

    def _flags():
        return {m["name"]: m["server_side"] for m in client.get("/api/mods").json()["mods"]}

    assert _flags() == {"@CF": False, "@DayZ-Dynamic-AI-Addon": False}

    resp = client.post("/api/mods/@DayZ-Dynamic-AI-Addon/server-side", json={"server_side": True})
    assert resp.status_code == 200
    assert resp.json()["server_side"] is True
    from app.shared.config import DayzConfig

    assert server_only_names(sample_config) == ["@DayZ-Dynamic-AI-Addon"]
    assert _flags()["@DayZ-Dynamic-AI-Addon"] is True

    resp = client.post("/api/mods/@DayZ-Dynamic-AI-Addon/server-side", json={"server_side": False})
    assert resp.json()["server_side"] is False
    assert server_only_names(sample_config) == []
    assert regen_calls == [["@DayZ-Dynamic-AI-Addon"], []]


def test_server_side_toggle_refuses_a_mod_not_in_mods_txt(client, sample_config, monkeypatch):
    write_test_mods(sample_config, ['@CF'])
    sample_config.save()
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files_async",
        lambda *a, **k: pytest.fail("must not rebuild the unit"),
    )
    resp = client.post("/api/mods/@Nope/server-side", json={"server_side": True})
    assert resp.status_code == 404
    from app.shared.config import DayzConfig

    assert server_only_names(sample_config) == []


def test_load_order_round_trip(client, monkeypatch, sample_config):
    async def _fake_service(runner, config, **kw):
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)

    resp = client.get("/api/mods/load-order")
    assert resp.status_code == 200
    assert "order" in resp.json() and "deps" in resp.json()

    resp = client.put("/api/mods/load-order", json={"order": ["@CF", "@TraderPlus"]})
    assert resp.status_code == 200
    from app.shared.config import DayzConfig

    assert pinned_names(sample_config) == ["@CF", "@TraderPlus"]


def test_load_order_rows_keep_a_disabled_mod_in_place(client, sample_config):
    """F78: `order` drops a disabled mod, `rows` keeps it where it would load,
    so the Installed table does not move the row that was just turned Off."""
    write_test_mods(sample_config, ['@CF', '@Alpha', '@Beta', '@Gamma'])
    set_test_flags(sample_config, pinned=["@CF", "@Gamma", "@Beta", "@Alpha"])
    sample_config.save()
    before = client.get("/api/mods/load-order").json()

    set_test_flags(sample_config, disabled=["@Beta"])
    sample_config.save()
    after = client.get("/api/mods/load-order").json()

    assert "@Beta" not in after["order"]
    assert after["rows"] == before["rows"] == before["order"]
    assert after["rows"].index("@Beta") == before["order"].index("@Beta")


def test_manual_load_order_switches_on_without_moving_anything(client, sample_config, monkeypatch):
    """plans/49: the switch alone keeps the order, rebuilds the unit and says so."""
    regenerated = []

    async def _fake_service(runner, config, **kw):
        regenerated.append(is_manual(config))
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)
    write_test_mods(sample_config, ['@CF', '@Alpha'])
    sample_config.save()
    before = client.get("/api/mods/load-order").json()
    assert before["manual"] is False

    assert client.put("/api/mods/load-order", json={"manual": True}).status_code == 200

    after = client.get("/api/mods/load-order").json()
    assert after["manual"] is True
    assert after["order"] == before["order"]
    assert regenerated == [True]
    from app.shared.config import DayzConfig

    assert pinned_names(sample_config) == before["rows"]


def test_load_order_put_needs_an_order_or_the_switch(client):
    assert client.put("/api/mods/load-order", json={}).status_code == 422


def test_load_order_writes_are_admin_only():
    from app.web.permissions import required_role

    assert required_role("PUT", "/api/mods/load-order") == "admin"


def test_missing_deps_empty_when_no_mod_deps(client):
    resp = client.get("/api/mods/missing-deps", params={"names": "@CF,@TraderPlus"})
    assert resp.status_code == 200
    assert resp.json() == {"missing": []}


def test_conflicts_shape(client):
    resp = client.get("/api/mods/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("items", "rpt_file", "mods_scanned", "mod_list", "scan_duration_ms", "check_statuses"):
        assert key in body


def test_conflicts_carry_the_dependency_edges_the_page_renders(client, monkeypatch):
    """Both Conflicts surfaces draw the scan's dependency graph from one
    component, so this payload has to keep carrying it — the Doctor report
    flattens the same data to `dependencies`."""
    from app.modmgr.mod_conflict import DepEdge, PboScanDebug, ScanResult

    scan = ScanResult(
        pbo_debug=PboScanDebug(
            pbos_scanned=282,
            dep_edges=[DepEdge(mod="@DayZ-Expansion-Core", depends_on="@Dabs Framework", source="PBO")],
        )
    )
    monkeypatch.setattr("app.modmgr.mod_conflict.scan_conflicts", lambda config, fetch_workshop=False: scan)
    debug = client.get("/api/mods/conflicts").json()["pbo_debug"]
    assert debug["pbos_scanned"] == 282
    assert debug["dep_edges"] == [
        {"mod": "@DayZ-Expansion-Core", "depends_on": "@Dabs Framework", "source": "PBO"}
    ]


def test_conflicts_items_carry_a_fix(client, monkeypatch):
    # Same remediation payload GET /api/doctor attaches to the same findings —
    # both Conflicts tables render it through one component.
    from app.modmgr.mod_conflict import ConflictItem, ScanResult

    scan = ScanResult(
        items=[
            ConflictItem(
                severity="warning",
                category="missing_bikey",
                message="Missing bikey(s): tp.bikey",
                mod_name="@TraderPlus",
            )
        ]
    )
    monkeypatch.setattr("app.modmgr.mod_conflict.scan_conflicts", lambda config, fetch_workshop=False: scan)
    item = client.get("/api/mods/conflicts").json()["items"][0]
    assert item["fix"]["how"]
    assert [a["kind"] for a in item["fix"]["actions"]] == ["copy_bikeys", "install_mods"]


def test_copy_bikeys_syncs_the_keys_dir(client, sample_config):
    # The "Copy bikeys" fix behind a missing_bikey finding: keys/ is rebuilt
    # from the enabled mods, so a stale key goes and the mod's own key lands.
    server_dir = sample_config.server_dir_path
    mod_dir = server_dir / "@CF" / "Keys"
    mod_dir.mkdir(parents=True)
    (mod_dir / "cf.bikey").write_bytes(b"key")
    keys_dir = sample_config.keys_dir
    keys_dir.mkdir(parents=True, exist_ok=True)
    (keys_dir / "stale.bikey").write_bytes(b"old")
    write_test_mods(sample_config, ['@CF'])

    resp = client.post("/api/mods/copy-bikeys", json={})
    assert resp.status_code == 200
    assert resp.json() == {"removed": 1, "copied": 1}
    assert (keys_dir / "cf.bikey").exists()
    assert not (keys_dir / "stale.bikey").exists()


def test_copy_bikeys_requires_a_session(app_and_config):
    assert TestClient(app_and_config).post("/api/mods/copy-bikeys", json={}).status_code == 401


def test_updates_empty_when_no_cache_file(client):
    resp = client.get("/api/mods/updates")
    assert resp.status_code == 200
    assert resp.json() == {
        "updates": [], "checked": 0, "total": 0, "checked_at": None, "api_key_set": False,
    }


def test_updates_say_whether_a_key_is_set_never_the_key(client, sample_config):
    sample_config.steam_api_key = "the-secret-key"
    resp = client.get("/api/mods/updates")
    assert resp.json()["api_key_set"] is True
    assert "the-secret-key" not in resp.text


def test_check_updates_uses_detect_mod_updates(client, monkeypatch):
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck

    monkeypatch.setattr(
        "app.modmgr.mod_ops.detect_mod_updates",
        lambda config: UpdateCheck(
            updates=[ModUpdateInfo(mod_id="1", title="Foo", remote_updated=100, local_updated=50)],
            checked=24, total=25, checked_at=1234,
        ),
    )
    resp = client.post("/api/mods/check-updates")
    assert resp.status_code == 200
    data = resp.json()
    body = data["updates"]
    assert len(body) == 1
    assert body[0]["mod_id"] == "1"
    assert "age_display" in body[0]
    assert (data["checked"], data["total"], data["checked_at"]) == (24, 25, 1234)
    # ...and GET serves the same answer back, with its timestamp.
    assert client.get("/api/mods/updates").json()["checked_at"] == 1234


def test_check_updates_without_a_key_is_409_not_no_updates(client):
    """plans/50 B (F17): this used to answer {"updates": []}, shown as
    "No updates detected." for a check that never ran."""
    resp = client.post("/api/mods/check-updates")
    assert resp.status_code == 409
    assert "steam_api_key" in resp.json()["detail"]


@pytest.mark.parametrize("reason,status", [("rejected", 502), ("unreachable", 502), ("no_api_key", 409)])
def test_a_failed_check_leaves_the_last_answer_byte_identical(client, monkeypatch, reason, status):
    from app.modmgr import workshop_api
    from app.modmgr.workshop_api import ModUpdateInfo, UpdateCheck
    from app.shared.paths import CONFIG_DIR

    path = CONFIG_DIR / "mod_updates.json"
    workshop_api.save_detected_updates(
        path, UpdateCheck(updates=[ModUpdateInfo("1", "A", 10, 5)], checked=1, total=1, checked_at=5),
    )
    before = path.read_bytes()

    def _fail(config):
        raise workshop_api.UpdateCheckError(reason, f"failed: {reason}")

    monkeypatch.setattr("app.modmgr.mod_ops.detect_mod_updates", _fail)
    resp = client.post("/api/mods/check-updates")
    assert resp.status_code == status
    assert resp.json()["detail"] == f"failed: {reason}"
    assert path.read_bytes() == before


def _mock_install_ops(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modmgr.mod_ops.resolve_mod_workshop_id", lambda config, name: ("42", True)
    )

    async def _fake_download(runner, config, mod_id, mod_name, max_retries=5):
        return True

    monkeypatch.setattr("app.web.routers.mods.download_mod_async", _fake_download)
    monkeypatch.setattr("app.ops.fileops.copy_mod_to_server", lambda config, name, cached_ids, log=None, **k: True)
    monkeypatch.setattr("app.ops.fileops.copy_bikey_files", lambda config, log=None: None)
    monkeypatch.setattr("app.modmgr.mods.get_current_mission", lambda cfg_path: "dayzOffline.chernarusplus")
    monkeypatch.setattr("app.modmgr.mods.require_mission", lambda config: "dayzOffline.chernarusplus")
    monkeypatch.setattr("app.xml.xml_economy.merge_economy_core", lambda *a, **kw: None)
    monkeypatch.setattr("app.commands.run._merge_all_xml_configs", lambda config, mission, disabled, log=None: [])

    async def _fake_service(runner, config, **kw):
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)


def test_install_mods_job_end_to_end(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["CF"]})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@CF"]



@pytest.mark.parametrize("reinstall", [True, False])
def test_reinstall_deletes_the_cached_copy_before_the_download(app_and_config, monkeypatch, sample_config, tmp_path, reinstall):
    # The mod pages' Reinstall: an update keeps a cached copy SteamCMD thinks
    # is current, so a broken download stays broken. Reinstall drops it first.
    _mock_install_ops(monkeypatch)
    content = tmp_path / "steamapps" / "workshop" / "content" / "221100"
    cached = content / "42"
    (cached / "addons").mkdir(parents=True)
    sample_config.workshop_content_dir = str(content)
    seen = []

    async def _download(runner, config, mod_id, mod_name, max_retries=5):
        seen.append(cached.exists())
        return True

    monkeypatch.setattr("app.web.routers.mods.download_mod_async", _download)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install", json={"names": ["CF"], "install_deps": False, "reinstall": reinstall}
        )
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["name"] == ("Reinstall mods" if reinstall else "Install mods")
    assert seen == [not reinstall]


def test_the_install_log_shows_the_bikey_line_without_rich_markup(app_and_config, monkeypatch):
    # fileops builds its lines for a Rich console; the job log renders them
    # raw, so the install showed "[green]✓ Removed …[/green]" (browser pass,
    # 2026 Q4). fileops hands a caller's log plain text itself now.
    from app.shared.cli_ui import markup_emitter

    _mock_install_ops(monkeypatch)
    monkeypatch.setattr(
        "app.ops.fileops.copy_bikey_files",
        lambda config, log=None: markup_emitter(log)(
            "[green]✓ Removed 1 old bikey(s), copied 2 new bikey(s).[/green]"
        ),
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["CF"]})
        body = _poll_job(client, resp.json()["job_id"])

    lines = [entry["line"] for entry in body["log"]]
    assert "✓ Removed 1 old bikey(s), copied 2 new bikey(s)." in lines
    assert not any("[green]" in line for line in lines)

def test_uninstall_mods_job_end_to_end(app_and_config, monkeypatch, sample_config):
    write_test_mods(sample_config, ['@CF'])
    sample_config.save()

    calls = []
    monkeypatch.setattr(
        "app.modmgr.mod_ops.uninstall_mods",
        lambda config, names, on_mod=None, log=None: calls.append(names),
    )

    async def _fake_service(runner, config, **kw):
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/uninstall", json={"names": ["@CF"]})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert calls == [["@CF"]]


def test_install_by_id_resolves_name(app_and_config, monkeypatch, sample_config):
    _mock_install_ops(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={
                "ids": ["2458896948"],
                "id_names": {"2458896948": "@TraderPlus"},
                "install_deps": False,
            },
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@TraderPlus"]
    # The id→name mapping is cached for future resolution.

    assert modlist.workshop_ids(sample_config).get("@TraderPlus") == "2458896948"


@pytest.mark.parametrize(
    "body",
    [
        {"names": ["@../outside"]},
        {"names": ["@a/b"]},
        {"ids": ["../123"]},
        {"ids": ["123"], "id_names": {"123": "@../outside"}},
    ],
)
def test_install_refuses_a_name_or_id_that_is_not_one_folder(app_and_config, sample_config, body):
    """The name becomes server_dir / name and the id workshop_dir / id. Refused
    before the job starts, so nothing reaches mods.txt or the id cache."""
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={**body, "install_deps": False})
    assert resp.status_code == 400
    assert not sample_config.paths.mods_json.exists()
    assert learned_ids() == {}


@pytest.mark.parametrize("name", ["@../outside", "../@x", "@a/b", "@a\\b"])
def test_add_refuses_a_name_that_is_not_one_folder(client, sample_config, name):
    resp = client.post("/api/mods", json={"name": name})
    assert resp.status_code == 400
    assert not sample_config.paths.mods_json.exists()


def test_install_by_id_names_from_the_registry(app_and_config, monkeypatch):
    # 2026 Q3: Market, AI and Hardline were absent from the seed registry and
    # from the server's cache, so all three installed as @<id> folders.
    _mock_install_ops(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={"ids": ["2792982069"], "install_deps": False},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@DayZ-Expansion-AI"]


def test_installing_a_catalogued_server_side_mod_marks_it(app_and_config, monkeypatch):
    # plans/47 A: the catalog knows Dynamic AI is server-only, so the install
    # marks it and the unit is rebuilt from the config that carries the mark.
    _mock_install_ops(monkeypatch)
    svc_seen = []

    async def _fake_service(runner, config, **kw):
        svc_seen.append(list(server_only_names(config)))
        return True

    monkeypatch.setattr("app.server.service_ops.generate_service_files_async", _fake_service)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={"ids": ["2874589934"], "install_deps": False},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@DayZ-Dynamic-AI-Addon"]
    assert server_only_names(app_and_config.state.ctx.config) == ["@DayZ-Dynamic-AI-Addon"]
    assert svc_seen == [["@DayZ-Dynamic-AI-Addon"]]
    assert any("-serverMod=" in entry["line"] for entry in body["log"]), body["log"]


def test_install_by_id_ignores_a_placeholder_name_in_the_cache(app_and_config, monkeypatch, sample_config):
    _mock_install_ops(monkeypatch)
    # What the pre-fix job wrote on a server that installed this once already.
    write_test_ids({'@2792982069': '2792982069'})

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={"ids": ["2792982069"], "install_deps": False},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@DayZ-Expansion-AI"]

    assert modlist.workshop_ids(app_and_config.state.ctx.config).get("@DayZ-Expansion-AI") == "2792982069"


def test_install_by_id_falls_back_to_the_id_when_no_name_is_known(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={"ids": ["9999999999"], "install_deps": False},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@9999999999"]


def test_install_fails_when_every_mod_was_skipped(app_and_config, monkeypatch):
    # 2026 Q3: three mods logged "No workshop ID found for @X — skipping." and
    # the job still finished "success", so they looked installed until someone
    # listed the folders.
    _mock_install_ops(monkeypatch)
    monkeypatch.setattr("app.modmgr.mod_ops.resolve_mod_workshop_id", lambda config, name: (None, False))

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["@DayZ-Expansion-AI"]})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "failed", body
    assert "@DayZ-Expansion-AI" in (body.get("error") or "")


def test_install_reports_what_it_skipped(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)
    monkeypatch.setattr(
        "app.modmgr.mod_ops.resolve_mod_workshop_id",
        lambda config, name: (("42", True) if name == "@CF" else (None, False)),
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["@CF", "@Nope"]})
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["installed"] == ["@CF"]
    assert body["result"]["skipped"] == ["@Nope"]


def test_install_pulls_missing_deps(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={
                "ids": ["2458896948"],  # @TraderPlus — seed MOD_DEPS requires @CF
                "id_names": {"2458896948": "@TraderPlus"},
                "install_deps": True,
            },
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert set(body["result"]["installed"]) == {"@TraderPlus", "@CF"}


def _spy_merge(monkeypatch) -> list[object]:
    """Record every mission-XML merge the install job runs."""
    merges: list[object] = []
    monkeypatch.setattr(
        "app.ops.pipeline._merge_xml_sync",
        lambda ctx: merges.append(ctx) or "dayzOffline.chernarusplus",
    )
    return merges


def test_install_configures_xml_by_default(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)
    merges = _spy_merge(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["CF"]})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert len(merges) == 1


def test_install_can_skip_xml_configuration(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)
    merges = _spy_merge(monkeypatch)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["CF"], "configure_xml": False})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    # Mods still installed — only the merge is skipped, and the log says so.
    assert body["result"]["installed"] == ["@CF"]
    assert merges == []
    assert any("Skipping mod XML configuration" in entry["line"] for entry in body["log"])


@pytest.mark.parametrize("running", [True, False])
def test_an_install_while_the_server_runs_says_to_restart(app_and_config, monkeypatch, running):
    """Night test 2026-11-22: a mod installed while the server ran gave no
    hint that it loads only at the next start."""
    from app.web.routers import mods as mods_router

    _mock_install_ops(monkeypatch)
    monkeypatch.setattr(mods_router.service_control, "is_server_running", lambda config=None: running)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={"ids": ["2458896948"], "id_names": {"2458896948": "@TraderPlus"}, "install_deps": False},
        )
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert ("restart it to load the new mods" in (body["result"].get("notice") or "")) is running


def test_install_copies_traderplus_config(app_and_config, monkeypatch, sample_config):
    _mock_install_ops(monkeypatch)
    src = sample_config.server_dir_path / "@TraderPlus" / "ServerProfile" / "TraderPlus" / "TraderPlusConfig"
    src.mkdir(parents=True)
    (src / "x.json").write_text("{}")

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={
                "ids": ["2458896948"],
                "id_names": {"2458896948": "@TraderPlus"},
                "install_deps": False,
            },
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    dest = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus" / "TraderPlusConfig" / "x.json"
    assert dest.is_file()


def test_cached_endpoint_lists_workshop_folder(client, sample_config, tmp_path):
    # Cached now sources the SteamCMD workshop *folder*, not cached-mod-ids.txt.
    # cached-mod-ids only resolves the id -> @name for display.
    ws = tmp_path / "workshop"
    sample_config.workshop_content_dir = str(ws)
    write_test_ids({'@CF': '111', '@TraderPlus': '222'})
    write_test_mods(sample_config, ['@CF'])
    for wid in ("111", "222", "999"):
        (ws / wid).mkdir(parents=True)

    resp = client.get("/api/mods/cached")
    assert resp.status_code == 200
    by_name = {m["name"]: m["id"] for m in resp.json()["mods"]}
    assert by_name.get("@TraderPlus") == "222"  # downloaded, not installed -> listed
    assert "@CF" not in by_name  # installed -> excluded
    assert by_name.get("@[999]") == "999"  # unknown name -> @[wid] fallback


def test_cached_endpoint_ignores_cached_ids_without_workshop_folder(client, sample_config, tmp_path):
    # A cached-mod-ids.txt entry with no matching workshop folder must NOT appear.
    sample_config.workshop_content_dir = str(tmp_path / "empty-workshop")
    (tmp_path / "empty-workshop").mkdir()
    write_test_ids({'@TraderPlus': '222'})
    write_test_mods(sample_config, [])

    resp = client.get("/api/mods/cached")
    assert resp.status_code == 200
    assert resp.json()["mods"] == []


def test_installed_mod_info_tooltip(client, sample_config):
    # A per-mod tooltip is built from the mod's readable mod.cpp/meta.cpp.
    write_test_mods(sample_config, ['@CoolMod'])
    mod_dir = sample_config.server_dir_path / "@CoolMod"
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "mod.cpp").write_text('name = "Cool Mod";\nauthor = "Me";\nversion = "1.2";\n')

    resp = client.get("/api/mods")
    m = next(x for x in resp.json()["mods"] if x["name"] == "@CoolMod")
    assert "Cool Mod" in m["info"]
    assert "by Me" in m["info"]


def test_known_endpoint_includes_seed_excludes_installed(client, sample_config):
    write_test_mods(sample_config, ['@CF'])

    resp = client.get("/api/mods/known")
    assert resp.status_code == 200
    names = {m["name"] for m in resp.json()["mods"]}
    assert "@TraderPlus" in names  # from the seed registry
    assert "@CF" not in names  # already installed


def test_install_reports_missing_required_addons(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)
    from app.modmgr.mod_conflict import ConflictItem

    def _fake_check(config):
        return (
            [
                ConflictItem(
                    severity="warning",
                    category="required_addon",
                    message="Requires addon 'CF_Modules' — not found in installed mods",
                    mod_name="@CF",
                )
            ],
            None,
        )

    monkeypatch.setattr("app.modmgr.mod_conflict.check_required_addons", _fake_check)

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(
            "/api/mods/install",
            json={"names": ["CF"], "install_deps": False},
        )
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["missing_addons"] == [
        "Requires addon 'CF_Modules' — not found in installed mods"
    ]


def test_install_conflict_returns_409(app_and_config, monkeypatch):
    _mock_install_ops(monkeypatch)

    def _slow_resolve(config, name):
        time.sleep(0.2)
        return "42", True

    monkeypatch.setattr("app.modmgr.mod_ops.resolve_mod_workshop_id", _slow_resolve)

    with TestClient(app_and_config) as client:
        _login(client)
        first = client.post("/api/mods/install", json={"names": ["CF"]})
        assert first.status_code == 200
        second = client.post("/api/mods/install", json={"names": ["TraderPlus"]})
        assert second.status_code == 409
        _poll_job(client, first.json()["job_id"])


# --- XML re-configure (Mods page "Re-configure" buttons) --------------------


@pytest.fixture()
def bike_mod_server(sample_config):
    """One installed mod contributing both a CE-linked and a merged XML file."""
    from tests.conftest import write_xml

    mission_dir = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    write_xml(mission_dir / "cfgeventspawns.xml", '<?xml version="1.0"?><eventposdef/>')
    write_xml(
        mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    mod = sample_config.server_dir_path / "@Bicycles"
    write_xml(mod / "types.xml", '<?xml version="1.0"?><types><type name="Old_Bike"/></types>')
    write_xml(
        mod / "cfgeventspawns_Chernarus.xml",
        '<?xml version="1.0"?><events><event name="E"><pos x="1" y="2" z="3" a="0"/></event></events>',
    )
    write_xml(mod / "readme.xml", '<?xml version="1.0"?><mysettings><foo/></mysettings>')
    write_test_mods(sample_config, ['@Bicycles'])
    sample_config.save()
    return sample_config


def test_xml_status_counts_per_mod(client, bike_mod_server):
    resp = client.get("/api/mods/xml-status")
    assert resp.status_code == 200
    entry = resp.json()["mods"]["@Bicycles"]
    assert entry["files"] == 3
    # Nothing merged yet: both economy files are recognised but unapplied, and
    # readme.xml is not an economy file at all.
    assert entry["pending"] == 2
    assert entry["skipped"] == 1
    assert entry["actionable"] == 2


def test_xml_status_after_merge(app_and_config, bike_mod_server):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/@Bicycles/xml/merge")
        assert resp.status_code == 200
        _poll_job(client, resp.json()["job_id"])
        entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]

    assert entry["linked"] == 1     # types.xml → custom/bicycles-types.xml
    assert entry["merged"] == 1     # cfgeventspawns_Chernarus.xml
    assert entry["pending"] == 0
    assert entry["actionable"] == 2


def test_merge_all_xml_job(app_and_config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.ops.pipeline._merge_xml_sync",
        lambda ctx: calls.append(ctx) or "dayzOffline.chernarusplus",
    )

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/xml/merge")
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["mission"] == "dayzOffline.chernarusplus"
    assert len(calls) == 1


def test_merge_one_mod_xml_job(app_and_config, bike_mod_server):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/@Bicycles/xml/merge")
        assert resp.status_code == 200
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert body["result"]["mod"] == "@Bicycles"
    assert body["result"]["linked"] == ["bicycles-types.xml"]
    assert body["result"]["failed"] == []

    mission_dir = bike_mod_server.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    assert (mission_dir / "custom" / "bicycles-types.xml").exists()
    assert "START MOD: @Bicycles" in (mission_dir / "cfgeventspawns.xml").read_text()


def test_merge_one_mod_xml_unknown_mod_is_404(client, bike_mod_server):
    resp = client.post("/api/mods/@Nope/xml/merge")
    assert resp.status_code == 404


def test_merge_one_mod_xml_disabled_mod_is_400(client, bike_mod_server):
    set_test_flags(bike_mod_server, disabled=["@Bicycles"])
    bike_mod_server.save()

    resp = client.post("/api/mods/@Bicycles/xml/merge")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_xml_status_counts_files_the_admin_turned_off(app_and_config, bike_mod_server):
    """A turned-off file is off: not actionable, and not skipped either."""
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, client.post("/api/mods/@Bicycles/xml/merge").json()["job_id"])
        client.put("/api/modxml/custom/bicycles-types.xml/state", json={"state": "deleted"})
        client.put(
            "/api/modxml/source/state",
            json={"path": "@Bicycles/cfgeventspawns_Chernarus.xml", "state": "excluded"},
        )
        entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]

    assert entry["off"] == 2  # one CE tile + one direct-merge row, counted once each
    assert entry["actionable"] == 0
    assert entry["pending"] == 0  # nothing a Re-configure could fix
    assert entry["skipped"] == 1  # readme.xml only
    assert _bucket_total(entry) == entry["files"]


# The buckets a mod's XML files are split into: each file is in exactly one.
_XML_BUCKETS = ("linked", "merged", "disabled", "pending", "off", "skipped")


def _bucket_total(entry: dict) -> int:
    return sum(entry[k] for k in _XML_BUCKETS)


def test_xml_status_counts_a_keep_mine_file_once(app_and_config, bike_mod_server):
    """mods-11: after Adopt's "Keep mine" the mod read
    "2 file(s)" with 1 off and 2 skipped, because the turned-off types file
    was counted as off and as skipped."""
    from app.xml import xml_manual

    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, client.post("/api/mods/@Bicycles/xml/merge").json()["job_id"])
        rows = client.get("/api/modxml").json()["files"]
        source = next(r["source"] for r in rows if r["mod"] == "@Bicycles" and r["group"] == "linked")
        # What adopt.keep_pasted writes: the source state only, no tile state.
        xml_manual.set_source_state(source, "excluded", manual_dir=manual_dir())
        entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]

    assert entry["files"] == 3
    assert entry["off"] == 1  # types.xml
    assert entry["merged"] == 1  # cfgeventspawns_Chernarus.xml
    assert entry["skipped"] == 1  # readme.xml only
    assert _bucket_total(entry) == entry["files"]


def test_xml_status_counts_a_disabled_mods_turned_off_file_as_off(client, bike_mod_server):
    """A disabled mod's file has status "disabled". Turned off, it is off and
    in no other bucket, so it does not ask for a Re-configure either."""
    from app.xml import xml_manual

    xml_manual.set_source_state(
        "@Bicycles/cfgeventspawns_Chernarus.xml", "deleted", manual_dir=manual_dir(),
    )
    set_test_flags(bike_mod_server, disabled=["@Bicycles"])
    bike_mod_server.save()

    entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]
    assert entry["off"] == 1
    assert entry["disabled"] == 1 and entry["actionable"] == 1  # types.xml
    assert _bucket_total(entry) == entry["files"]


def test_xml_status_counts_a_ce_file_turned_off_by_its_source(app_and_config, bike_mod_server):
    """Adopt's "Keep mine" turns a mod's types file off
    by its source; it read as "skipped", with no off count and no Restore."""
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, client.post("/api/mods/@Bicycles/xml/merge").json()["job_id"])
        rows = client.get("/api/modxml").json()["files"]
        source = next(r["source"] for r in rows if r["mod"] == "@Bicycles" and r["group"] == "linked")
        client.put("/api/modxml/source/state", json={"path": source, "state": "excluded"})
        entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]
    assert entry["off"] == 1


def test_restore_turns_a_mods_files_back_on(app_and_config, bike_mod_server):
    mission_dir = bike_mod_server.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"

    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, client.post("/api/mods/@Bicycles/xml/merge").json()["job_id"])
        client.delete("/api/modxml/custom/bicycles-types.xml")
        client.delete("/api/modxml/source?path=@Bicycles/cfgeventspawns_Chernarus.xml")
        assert not (mission_dir / "custom" / "bicycles-types.xml").exists()

        resp = client.post("/api/mods/@Bicycles/xml/restore")
        assert resp.status_code == 200, resp.text
        assert resp.json()["restored"] == 2 and resp.json()["applied"] is True
        entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]

    assert entry["off"] == 0
    assert (mission_dir / "custom" / "bicycles-types.xml").is_file()
    assert "START MOD: @Bicycles" in (mission_dir / "cfgeventspawns.xml").read_text()


def test_restore_only_touches_the_named_mod(client, bike_mod_server):
    from app.xml import xml_manual

    xml_manual.set_custom_state("other-types.xml", "deleted", manual_dir=manual_dir())
    xml_manual.set_source_state("@Other/types.xml", "excluded", manual_dir=manual_dir())

    client.post("/api/mods/@Bicycles/xml/restore")

    assert xml_manual.load_exclusions(manual_dir=manual_dir()) == {"other-types.xml"}
    assert xml_manual.load_source_exclusions(manual_dir=manual_dir()) == {"@Other/types.xml"}


def test_restore_records_without_applying_for_a_disabled_mod(client, bike_mod_server):
    """A scoped merge would delete the mod's parked DISABLED wrappers."""
    from app.xml import xml_manual

    xml_manual.set_source_state(
        "@Bicycles/cfgeventspawns_Chernarus.xml", "deleted", manual_dir=manual_dir(),
    )
    set_test_flags(bike_mod_server, disabled=["@Bicycles"])
    bike_mod_server.save()

    body = client.post("/api/mods/@Bicycles/xml/restore").json()
    assert body["restored"] == 1
    assert body["applied"] is False and body["reason"] == "mod disabled"
    assert xml_manual.load_source_exclusions(manual_dir=manual_dir()) == set()


def test_xml_status_counts_manual_files_bound_to_a_mod(client, bike_mod_server):
    """The Mods page is where a user would look for "does this mod have mine?"."""
    from app.xml import xml_manual

    xml_manual.create_file(
        "bike-loot", "types", '<?xml version="1.0"?><types><type name="Mine"/></types>',
        mod="@Bicycles",
        manual_dir=manual_dir(),
    )
    entry = client.get("/api/mods/xml-status").json()["mods"]["@Bicycles"]
    assert entry["manual"] == 1
    # It is the admin's own XML, not the mod's — it must not inflate the counts
    # the Re-configure button acts on.
    assert entry["files"] == 3 and entry["actionable"] == 2


def test_restore_unknown_mod_is_404(client, bike_mod_server):
    assert client.post("/api/mods/@Nope/xml/restore").status_code == 404


# --- Per-mod detail (the /mods/:id page) ------------------------------------


@pytest.fixture()
def detail_server(sample_config, tmp_path):
    """One installed mod (@CF, with metadata + a workshop copy) and one mod that
    is only present in the SteamCMD workshop cache (@TraderPlus).

    workshop_content_dir MUST be pinned — DayzConfig.workshop_dir otherwise
    auto-detects the host's real Steam install.
    """
    ws = tmp_path / "workshop"
    sample_config.workshop_content_dir = str(ws)
    write_test_ids({'@CF': '111', '@TraderPlus': '222'})
    write_test_mods(sample_config, ['@CF'])
    sample_config.save()

    mod_dir = sample_config.server_dir_path / "@CF"
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "mod.cpp").write_text('name = "Community Framework";\nauthor = "Jacob";\nversion = "1.2";\n')
    (mod_dir / "payload.pbo").write_text("x" * 10)
    for wid in ("111", "222"):
        (ws / wid).mkdir(parents=True)
        (ws / wid / "payload.pbo").write_text("y" * 5)
    return sample_config


def test_detail_by_name_for_installed_mod(client, detail_server):
    resp = client.get("/api/mods/@CF")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "@CF"
    assert body["id"] == "111"
    assert body["workshop_url"].endswith("?id=111")
    assert body["listed"] is True
    assert body["installed"] is True
    assert body["enabled"] is True
    assert body["cached"] is True
    assert body["meta"]["author"] == "Jacob"
    assert body["size_bytes"] > 0
    assert body["actions"]["disable"] is True
    assert body["actions"]["enable"] is False
    assert body["actions"]["uninstall"] is True
    assert body["actions"]["install"] is False
    assert body["actions"]["remove_cache"] is True


def test_detail_by_workshop_id_resolves_same_mod(client, detail_server):
    by_name = client.get("/api/mods/@CF").json()
    by_id = client.get("/api/mods/111").json()
    assert by_id["name"] == by_name["name"] == "@CF"
    assert by_id["installed"] is True


def test_detail_for_cached_only_mod_offers_install_and_cache_removal(client, detail_server):
    resp = client.get("/api/mods/222")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "@TraderPlus"
    assert body["listed"] is False
    assert body["installed"] is False
    assert body["cached"] is True
    assert body["workshop_path"].endswith("/222")
    assert body["cached_size_bytes"] > 0
    assert body["actions"]["install"] is True
    assert body["actions"]["remove_cache"] is True
    assert body["actions"]["uninstall"] is False
    assert body["actions"]["reconfigure"] is False


def test_detail_carries_a_credit_only_for_a_listed_mod(client, detail_server, monkeypatch):
    """The mod page's credit line comes from seed_data.MOD_CREDITS, by Workshop ID."""
    from app.web.routers import mods as mods_router

    credit = {"text": "Made by someone.", "link_label": "example.org", "link_url": "https://example.org/"}
    monkeypatch.setattr(mods_router, "MOD_CREDITS", {"222": credit})
    assert client.get("/api/mods/222").json()["credit"] == credit
    assert client.get("/api/mods/@CF").json()["credit"] is None


def test_thedmitri_mods_share_one_credit():
    """TraderPlus, RadZone and ToxicZone name TheDmitri and link his launcher over HTTPS."""
    from app.shared.seed_data import MOD_CREDITS

    ids = {"2458896948", "2294323732", "1752669393"}
    assert set(MOD_CREDITS) == ids
    for wid in ids:
        assert "TheDmitri" in MOD_CREDITS[wid]["text"]
        assert MOD_CREDITS[wid]["link_url"].startswith("https://")


def test_detail_reports_disabled_mod_and_refuses_reconfigure(client, detail_server):
    set_test_flags(detail_server, disabled=["@CF"])
    detail_server.save()

    body = client.get("/api/mods/@CF").json()
    assert body["enabled"] is False
    assert body["actions"]["enable"] is True
    assert body["actions"]["disable"] is False
    # A scoped XML merge is refused for a disabled mod, so the button is not offered.
    assert body["actions"]["reconfigure"] is False


def test_detail_reports_filesystem_timestamps(client, detail_server):
    body = client.get("/api/mods/@CF").json()
    ts = body["timestamps"]
    now = int(time.time())
    # Both copies were just written by the fixture.
    assert now - 60 <= ts["installed"] <= now + 60
    assert now - 60 <= ts["downloaded"] <= now + 60
    # No meta.cpp and no workshop manifest in this fixture.
    assert ts["built"] == 0
    assert ts["workshop_updated"] == 0


def test_detail_reports_built_and_workshop_revision(client, sample_config, tmp_path):  # noqa: D205
    """meta.cpp's DateTime.ToBinary() stamp, as a real Workshop mod has it,
    becomes a Unix epoch; the workshop ACF supplies the revision date of the
    downloaded copy."""
    steamapps = tmp_path / "steamapps" / "workshop"
    sample_config.workshop_content_dir = str(steamapps / "content" / "221100")
    write_test_ids({'@CF': '111'})
    write_test_mods(sample_config, ['@CF'])
    sample_config.save()

    mod_dir = sample_config.server_dir_path / "@CF"
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "mod.cpp").write_text('name = "Community Framework";\n')
    (mod_dir / "meta.cpp").write_text(
        "publishedid = 111;\r\ntimestamp = 5250960208020560000;\r\n"
    )
    (steamapps / "appworkshop_221100.acf").parent.mkdir(parents=True, exist_ok=True)
    (steamapps / "appworkshop_221100.acf").write_text(
        '"AppWorkshop"\n{\n\t"WorkshopItemsInstalled"\n\t{\n'
        '\t\t"111"\n\t\t{\n\t\t\t"timeupdated"\t\t"1700000000"\n\t\t}\n\t}\n}\n'
    )

    body = client.get("/api/mods/@CF").json()
    assert body["timestamps"]["built"] == 1791822159  # 2026-10-12 16:22:39 UTC
    assert body["timestamps"]["workshop_updated"] == 1700000000


def test_detail_reports_xml_tallies_and_deps(client, bike_mod_server):
    body = client.get("/api/mods/@Bicycles").json()
    assert body["xml"]["files"] == 3
    assert body["xml"]["actionable"] == 2
    assert body["order"] == 0
    assert body["order_total"] == 1
    assert body["requires"] == []
    assert body["required_by"] == []


def test_detail_unknown_name_and_id_are_404(client, detail_server):
    assert client.get("/api/mods/@Nope").status_code == 404
    assert client.get("/api/mods/9999999999").status_code == 404


@pytest.mark.parametrize("ref", ["%2E%2E%2F%2E%2E%2Fetc%2Fpasswd", "@a%2Fb", "@..", "%20"])
def test_detail_rejects_unsafe_refs(client, detail_server, ref):
    # The ref becomes a directory name downstream, so anything that isn't a bare
    # component must not resolve (never mind reach the filesystem).
    # (A literal ".." is not tested over HTTP — the client collapses it into the
    # list route before it is ever sent; see test_mod_ops.py for that case.)
    assert client.get(f"/api/mods/{ref}").status_code in (404, 422)


def test_detail_route_does_not_shadow_static_routes(client, detail_server):
    # GET /{ref} is a single-segment catch-all: if it is ever declared before
    # these, they stop resolving. Guard the declaration order.
    assert "tabs" in client.get("/api/mods/help").json()
    assert "mods" in client.get("/api/mods/cached").json()
    assert "mods" in client.get("/api/mods/known").json()
    assert "mods" in client.get("/api/mods/xml-status").json()
    assert "order" in client.get("/api/mods/load-order").json()
    assert "updates" in client.get("/api/mods/updates").json()
    assert "items" in client.get("/api/mods/conflicts").json()


def test_mod_files_lists_the_installed_folder(client, detail_server):
    (detail_server.server_dir_path / "@CF" / "config").mkdir(parents=True, exist_ok=True)
    (detail_server.server_dir_path / "@CF" / "config" / "settings.json").write_text("{}\n")

    body = client.get("/api/mods/@CF/files").json()
    assert body["source"] == "installed"
    rows = {f["path"]: f for f in body["files"]}
    # Nested paths are POSIX-relative to the mod dir, and only the text kinds
    # the viewer serves carry a language.
    assert rows["config/settings.json"]["language"] == "json"
    assert rows["mod.cpp"]["language"] == ""
    assert rows["payload.pbo"]["size"] == 10


def test_mod_files_fall_back_to_the_workshop_cache(client, detail_server):
    body = client.get("/api/mods/222/files").json()
    assert body["source"] == "cache"
    assert [f["path"] for f in body["files"]] == ["payload.pbo"]


def test_mod_files_join_the_xml_report(client, bike_mod_server):
    rows = {f["path"]: f for f in client.get("/api/mods/@Bicycles/files").json()["files"]}
    # Recognised but not merged into the mission yet.
    assert rows["types.xml"]["xml"]["status"] == "no_target"
    assert rows["types.xml"]["xml"]["kind"] == "types"
    # Same verdict the Mod XML report gives it — an XML the merge ignores.
    assert rows["readme.xml"]["xml"]["status"] == "ignored"


def test_mod_file_serves_text_but_refuses_other_kinds(client, detail_server):
    (detail_server.server_dir_path / "@CF" / "notes.md").write_text("# hi\n")

    body = client.get("/api/mods/@CF/file", params={"path": "notes.md"}).json()
    assert body["content"] == "# hi\n"
    assert body["language"] == "txt"
    assert body["truncated"] is False
    # A .cpp/.pbo is named in the listing but never served.
    assert client.get("/api/mods/@CF/file", params={"path": "mod.cpp"}).status_code == 400


def test_mod_file_reads_only_what_it_shows(client, detail_server):
    """plans/68 CON-14: the viewer read the whole file, then cut it to 1 MB,
    so a mod shipping a GB-sized .txt cost a GB per request."""
    import tracemalloc

    from app.modmgr import mod_ops

    big = 32 * 1024 * 1024
    path = detail_server.server_dir_path / "@CF" / "huge.txt"
    with open(path, "wb") as f:
        f.write(b"a" * (mod_ops.MAX_MOD_FILE_BYTES + 10))
        f.truncate(big)

    tracemalloc.start()
    try:
        body = client.get("/api/mods/@CF/file", params={"path": "huge.txt"}).json()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert body["size"] == big and body["truncated"] is True
    assert body["shown"] == mod_ops.MAX_MOD_FILE_BYTES
    assert body["content"] == "a" * mod_ops.MAX_MOD_FILE_BYTES
    assert peak < big // 2


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "nope.xml"])
def test_mod_file_rejects_paths_outside_the_mod(client, detail_server, path):
    assert client.get("/api/mods/@CF/file", params={"path": path}).status_code == 404


def test_enable_disable_unknown_mod_is_404(client, detail_server):
    assert client.post("/api/mods/@Nope/disable").status_code == 404
    assert client.post("/api/mods/@Nope/enable").status_code == 404
    # config.json must not have picked up the bogus name.
    detail_server.load()
    assert "@Nope" not in disabled_names(detail_server)


# ---------------------------------------------------------------------------
# Spawn test (plans/37)
# ---------------------------------------------------------------------------


@pytest.fixture()
def spawn_server(sample_config, tmp_mission_dir, monkeypatch):
    """@Bikes listed and installed, a mission with init.c + storage_1, server stopped."""
    write_test_mods(sample_config, ['@Bikes'])
    sample_config.save()
    (sample_config.server_dir_path / "@Bikes").mkdir()
    (tmp_mission_dir / "init.c").write_text("void main()\n{\n}\n")
    (tmp_mission_dir / "storage_1").mkdir()
    (tmp_mission_dir / "storage_1" / "players.db").write_bytes(b"p")
    monkeypatch.setattr(
        "app.modmgr.mod_conflict.extract_mod_classnames_cached",
        lambda server_dir, mods, *a, **k: {m: ["Bicycle", "BicycleBase"] for m in mods},
    )
    monkeypatch.setattr("app.ops.vps_readiness.mem_mb", lambda: (8192, 0))
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )
    return sample_config


def _spy_broadcasts(client, monkeypatch) -> list[dict]:
    sent: list[dict] = []
    hub = client.app.state.ctx.hub

    async def _spy(message, *args, **kwargs):
        sent.append(message)

    monkeypatch.setattr(hub, "broadcast", _spy)
    return sent


def test_spawn_test_info_shape(client, spawn_server):
    """The places come from the admin's own label file: Zedstead ships none (plans/86)."""
    from app.shared import paths

    user_dir = paths.locations_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "locations_chernarusplus.json").write_text(
        '[{"name": "brookside", "x": 5340.7, "z": 11380.0, "type": "Village"}]', encoding="utf-8")
    body = client.get("/api/mods/@Bikes/spawn-test").json()
    assert body == {
        "classes": ["Bicycle", "BicycleBase"],
        "warn_above": 100,
        "warn_reason": "8–16 GB RAM",
        "active": None,
        "storage_name": "storage_1",
        "locations": body["locations"],
        "map_size": 15360,
    }
    assert {"name": "brookside", "x": 5340.7, "z": 11380.0} in body["locations"]


def test_spawn_test_info_names_the_folder_instance_id_selects(client, spawn_server, sample_config):
    set_instance_id(sample_config.server_dir_path, 4)
    assert client.get("/api/mods/@Bikes/spawn-test").json()["storage_name"] == "storage_4"


def test_spawn_test_unknown_mod_is_404(client, spawn_server):
    assert client.get("/api/mods/@Nope/spawn-test").status_code == 404
    resp = client.post("/api/mods/@Nope/spawn-test", json={"classname": "Bicycle", "count": 1})
    assert resp.status_code == 404


def test_spawn_test_start_and_end_round_trip(client, spawn_server, tmp_mission_dir, monkeypatch):
    sent = _spy_broadcasts(client, monkeypatch)
    resp = client.post(
        "/api/mods/@Bikes/spawn-test", json={"classname": "Bicycle", "count": 500}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classname"] == "Bicycle" and body["count"] == 500
    assert "at your own risk" in body["warning"]  # above 100, and still started
    assert any("Spawn test armed" in line for line in body["log"])
    assert client.get("/api/mods/@Bikes/spawn-test").json()["active"]["count"] == 500

    resp = client.post("/api/mods/spawn-test/end")
    assert resp.status_code == 200
    assert resp.json()["storage"] == "restored"
    assert (tmp_mission_dir / "init.c").read_text() == "void main()\n{\n}\n"
    assert sent.count({"type": "mods_changed"}) == 2


def test_spawn_test_at_a_location(client, spawn_server, tmp_mission_dir):
    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "at_location", "classname": "Bicycle", "count": 2, "x": 5340.7,
              "z": 11380.0, "location": "Adamovka"},
    )
    assert resp.status_code == 200
    assert resp.json()["location"] == {"name": "Adamovka", "x": 5340.7, "z": 11380.0}
    assert "player.SetPosition(origin);" in (tmp_mission_dir / "init.c").read_text()
    active = client.get("/api/mods/@Bikes/spawn-test").json()["active"]
    assert active["mode"] == "at_location"
    assert client.post("/api/mods/spawn-test/end").status_code == 200


def test_spawn_test_at_a_location_needs_both_coordinates(client, spawn_server):
    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "at_location", "classname": "Bicycle", "count": 1, "x": 100},
    )
    assert resp.status_code == 422
    assert "needs both X and Z" in resp.json()["detail"]
    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "at_location", "classname": "Bicycle", "count": 1, "x": 100, "z": 99999},
    )
    assert resp.status_code == 422
    assert "off the map" in resp.json()["detail"]


def test_spawn_test_refused_while_the_server_runs(client, spawn_server, monkeypatch):
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: True
    )
    resp = client.post("/api/mods/@Bikes/spawn-test", json={"classname": "Bicycle", "count": 1})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Server is running. Stop it first."
    assert client.post("/api/mods/spawn-test/end").status_code == 409


def test_spawn_test_end_route_is_not_swallowed(client, spawn_server):
    # /spawn-test/end must reach its own handler, not a /{name}/... route.
    resp = client.post("/api/mods/spawn-test/end")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "No spawn test is active."


def test_spawn_test_bad_input_is_422(client, spawn_server):
    resp = client.post("/api/mods/@Bikes/spawn-test", json={"classname": "Car", "count": 1})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Car is not a class in @Bikes."
    resp = client.post("/api/mods/@Bikes/spawn-test", json={"classname": "Bicycle", "count": -1})
    assert resp.status_code == 422
    # "Only X" without a scope: the mode is known, the request is not usable.
    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "only_x", "classname": "Bicycle", "count": 1},
    )
    assert resp.status_code == 422
    assert "needs a scope" in resp.json()["detail"]
    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "sideways", "classname": "Bicycle", "count": 1},
    )
    assert resp.status_code == 422
    assert "Unknown spawn test mode" in resp.json()["detail"]


def test_spawn_test_only_x_needs_an_economy_entry(client, spawn_server, tmp_mission_dir):
    """The mission here has no types.xml at all, which is the same refusal as a
    class the economy has never heard of — and it must not arm the test."""
    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "only_x", "classname": "Bicycle", "count": 5, "scope": "loot"},
    )
    assert resp.status_code == 422
    assert "Use \"Spawn next to me\" instead." in resp.json()["detail"]
    assert client.get("/api/mods/@Bikes/spawn-test").json()["active"] is None


def test_spawn_test_only_x_round_trip(client, spawn_server, tmp_mission_dir):
    types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<types>\n'
        '  <type name="Bicycle"><nominal>3</nominal><min>1</min></type>\n'
        '  <type name="Hammer"><nominal>45</nominal><min>40</min></type>\n'
        "</types>\n"
    )
    (tmp_mission_dir / "db" / "types.xml").write_text(types_xml, encoding="utf-8")

    resp = client.post(
        "/api/mods/@Bikes/spawn-test",
        json={"mode": "only_x", "classname": "Bicycle", "count": 12, "scope": "loot"},
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["mode"] == "only_x" and body["scope"] == "loot"
    assert any("Only X armed" in line for line in body["log"])
    assert "<nominal>12</nominal>" in (tmp_mission_dir / "db" / "types.xml").read_text()

    assert client.post("/api/mods/spawn-test/end").status_code == 200
    assert (tmp_mission_dir / "db" / "types.xml").read_text() == types_xml


def test_spawn_test_runs_as_a_job_when_storage_is_large(app_and_config, spawn_server, monkeypatch):
    monkeypatch.setattr("app.web.routers.mods._SPAWN_TEST_JOB_BYTES", 0)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/@Bikes/spawn-test", json={"classname": "Bicycle", "count": 3})
        assert set(resp.json()) == {"job_id"}
        job = _poll_job(client, resp.json()["job_id"])
        assert job["status"] == "success"
        assert job["result"]["count"] == 3

        # A refusal inside a job fails the job with the same message.
        resp = client.post("/api/mods/@Bikes/spawn-test", json={"classname": "Bicycle", "count": 3})
        job = _poll_job(client, resp.json()["job_id"])
        assert job["status"] == "failed"

        job = _poll_job(client, client.post("/api/mods/spawn-test/end").json()["job_id"])
        assert job["status"] == "success"
        assert job["result"]["storage"] == "restored"


def test_mods_list_carries_the_active_spawn_test(client, spawn_server):
    assert client.get("/api/mods").json()["spawn_test"] is None
    client.post("/api/mods/@Bikes/spawn-test", json={"classname": "Bicycle", "count": 4})
    # The dashboard's Mods tile reads it from here rather than asking twice.
    active = client.get("/api/mods").json()["spawn_test"]
    assert (active["mod"], active["classname"], active["count"]) == ("@Bikes", "Bicycle", 4)
    client.post("/api/mods/spawn-test/end")


# ── GET /api/mods/expansion (the module picker) ────────────────────


def test_expansion_catalog_shape(client):
    body = client.get("/api/mods/expansion").json()
    assert len(body["modules"]) == 19
    assert [f["name"] for f in body["frameworks"]] == ["@CF", "@Dabs Framework"]
    assert body["installed"] == []

    core = next(m for m in body["modules"] if m["label"] == "Core")
    assert core["name"] == "@DayZ-Expansion-Core"
    assert core["id"] == "2291785308"
    assert core["requires"] == ["1559212036", "2545327648"]
    assert core["installed"] is False
    assert core["blurb"]

    # The picker locks rows off `requires`, so it must be the whole closure.
    vehicles = next(m for m in body["modules"] if m["label"] == "Vehicles")
    assert vehicles["requires"] == ["1559212036", "2545327648", "2291785308", "2116157322"]
    assert vehicles["classes"] == 549

    # Nothing installed yet, so both frameworks are still to come.
    assert [d["name"] for d in body["missing_deps"]] == ["@CF", "@Dabs Framework"]


def test_expansion_catalog_reports_installed_modules(client, sample_config):
    write_test_mods(sample_config, ['@CF', '@DayZ-Expansion-Core'])

    body = client.get("/api/mods/expansion").json()
    assert set(body["installed"]) == {"1559212036", "2291785308"}
    core = next(m for m in body["modules"] if m["label"] == "Core")
    assert core["installed"] is True
    # @Dabs Framework is listed but missing — Core cannot actually load.
    assert [d["name"] for d in body["missing_deps"]] == ["@Dabs Framework"]


def test_expansion_catalog_needs_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/mods/expansion").status_code == 401


@pytest.mark.parametrize(
    "method, url, body",
    [
        ("post", "/api/mods", {"name": "@NewMod"}),
        ("post", "/api/mods/@CF/enable", None),
        ("post", "/api/mods/@CF/disable", None),
        ("post", "/api/mods/@CF/server-side", {"server_side": True}),
        ("put", "/api/mods/load-order", {"order": ["@CF"]}),
    ],
)
def test_mod_list_writes_are_refused_while_a_job_runs(client, sample_config, monkeypatch, method, url, body):
    """An install or update job rewrites the mod list and the mission XML, so a
    toggle beside it would interleave its own merge and list write."""
    from app.web.jobs import Job

    write_test_mods(sample_config, ['@CF'])
    sample_config.save()
    before = modlist.to_json(modlist.load(sample_config))
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files_async",
        lambda *a, **k: pytest.fail("must not rebuild the unit"),
    )
    jobs = client.app.state.ctx.jobs
    jobs._active_exclusive = Job(id="busyjob", name="n", kind="k", hub=jobs.hub)
    try:
        resp = getattr(client, method)(url, **({"json": body} if body is not None else {}))
    finally:
        jobs._active_exclusive = None
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Another job is already running: busyjob"
    assert modlist.to_json(modlist.load(sample_config)) == before


@pytest.mark.parametrize(
    "url, target",
    [
        ("/api/mods/copy-bikeys", "app.ops.fileops.copy_bikey_files"),
        ("/api/mods/@CF/xml/restore", "app.xml.xml_manual.clear_mod_file_states"),
        ("/api/mods/rollback/restore", "app.ops.mod_rollback.restore"),
        ("/api/mods/rollback/discard", "app.ops.mod_rollback.discard"),
    ],
)
def test_mod_file_actions_are_refused_while_a_job_runs(client, sample_config, monkeypatch, url, target):
    """Keys, mod XML and the rollback stash are what an install or update job
    writes too."""
    from app.web.jobs import Job

    write_test_mods(sample_config, ['@CF'])
    sample_config.save()
    monkeypatch.setattr(target, lambda *a, **k: pytest.fail("must not run beside a job"))
    jobs = client.app.state.ctx.jobs
    jobs._active_exclusive = Job(id="busyjob", name="n", kind="k", hub=jobs.hub)
    try:
        resp = client.post(url)
    finally:
        jobs._active_exclusive = None
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Another job is already running: busyjob"
