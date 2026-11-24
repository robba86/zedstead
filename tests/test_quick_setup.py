"""Tests for Quick setup: the recommended mods and /api/quick-setup/*."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.modmgr import mods, modlist, recommended_mods
from app.modmgr.recommended_mods import AIRDROP_UPGRADED, RADZONE, TRADERPLUS, ModSet
from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS
from app.web.routers import quick_setup
from tests.test_web_mods import PASSWORD, _login, _mock_install_ops, _poll_job

ALL_SETS = (*recommended_mods.TESTED_SETS, *recommended_mods.CANDIDATE_SETS)
TESTED = ModSet((TRADERPLUS, AIRDROP_UPGRADED), ("Chernarus",), "1.30", "2026-11-10")
OTHER = ModSet((TRADERPLUS, RADZONE), ("Chernarus",), "1.30", "2026-11-10")


@pytest.fixture()
def app_and_config(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


@pytest.fixture()
def parts(monkeypatch) -> list[str]:
    """Patch each part of the job with a stub that records its name."""
    seen: list[str] = []

    class _Result:
        status = "success"
        error = ""
        label = "update_server"

    async def _run_pipeline(steps, ctx):
        # finalize installs the unit: without it a fresh Docker box cannot start.
        assert ctx.should_run("update_server") and ctx.should_run("finalize")
        assert not ctx.should_run("merge_xml")
        seen.append("server")
        return [_Result()], []

    async def _install(job, state, **kw):
        seen.append("mods")
        return {"installed": kw["requested_ids"], "already": [], "skipped": [], "missing_addons": []}

    def _preset(config, preset_id):
        seen.append("preset")
        return ["cfggameplay.json"]

    async def _start(config, before, emit=None):
        seen.append("start")
        return {"outcome": "joinable"}

    monkeypatch.setattr(quick_setup.pipeline, "run_pipeline", _run_pipeline)
    monkeypatch.setattr(quick_setup, "run_mod_install", _install)
    monkeypatch.setattr(quick_setup, "apply_preset_to_mission", _preset)
    monkeypatch.setattr(quick_setup.post_update, "snapshot", lambda config: None)
    monkeypatch.setattr(quick_setup.post_update, "start_and_check", _start)
    monkeypatch.setattr(quick_setup.service_control, "is_server_running", lambda config: False)
    monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: False)
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", (TESTED,))
    return seen


def _list_mods(config, *mods_to_add: tuple[str, str]) -> None:
    """Put mods in the mod list, as a stopped Apply or an admin leaves them."""

    def _add(state) -> None:
        for name, wid in mods_to_add:
            state.add(name, wid)

    modlist.update(config, _add)


def _apply(client: TestClient, **body):
    return client.post("/api/quick-setup/apply", json={"preset": "vanilla_balanced", **body})


# --- the list as data ---------------------------------------------------------


def test_every_recommended_mod_is_complete():
    assert recommended_mods.RECOMMENDED_MODS
    for mod in recommended_mods.RECOMMENDED_MODS:
        assert mods.is_workshop_id(mod.workshop_id)
        assert mod.name.startswith("@")
        assert mod.blurb and mod.slot and mod.terms_checked


def test_every_recommended_mod_uses_the_name_zsm_knows():
    known = modlist._known_ids()
    for mod in recommended_mods.RECOMMENDED_MODS:
        assert known[mod.name] == mod.workshop_id


@pytest.mark.parametrize("mod_set", ALL_SETS)
def test_every_set_follows_the_rules(mod_set):
    assert 2 <= len(mod_set.mods) <= 3
    found = [recommended_mods.get_mod(wid) for wid in mod_set.mods]
    assert all(found)
    slots = [mod.slot for mod in found]
    assert len(slots) == len(set(slots))
    assert mod_set.maps
    assert all(m in VANILLA_MAPS or m in WORKSHOP_MAPS for m in mod_set.maps)


@pytest.mark.parametrize("mod_set", recommended_mods.TESTED_SETS)
def test_every_tested_set_names_its_test(mod_set):
    assert mod_set.dayz_version and mod_set.tested_on


def test_candidates_are_offered_only_on_a_test_server(monkeypatch):
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", ())
    monkeypatch.delenv(recommended_mods.CANDIDATES_ENV, raising=False)
    assert recommended_mods.sets_for_map("Chernarus") == []
    monkeypatch.setenv(recommended_mods.CANDIDATES_ENV, "1")
    assert recommended_mods.sets_for_map("Chernarus") == list(recommended_mods.CANDIDATE_SETS)


# --- the roll -----------------------------------------------------------------


def test_a_seed_repeats_its_roll(monkeypatch):
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", (TESTED, OTHER))
    rolls = {recommended_mods.roll(seed, "Chernarus") for seed in range(30)}
    assert rolls == {TESTED, OTHER}
    assert recommended_mods.roll(7, "Chernarus") == recommended_mods.roll(7, "Chernarus")


def test_a_new_roll_shows_a_change_when_it_can(monkeypatch):
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", (TESTED, OTHER))
    for seed in range(20):
        assert recommended_mods.roll(seed, "Chernarus", previous=TESTED) == OTHER
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", (TESTED,))
    assert recommended_mods.roll(1, "Chernarus", previous=TESTED) == TESTED


def test_a_map_without_a_tested_set_rolls_nothing(monkeypatch):
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", (TESTED,))
    assert recommended_mods.roll(1, "Livonia") is None


# --- GET /mods ----------------------------------------------------------------


def test_the_card_shows_the_whole_list_with_dependencies(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        body = client.get("/api/quick-setup/mods?seed=1").json()
    assert body["map"] == "Chernarus"  # a server that is not installed gets the stock map
    assert body["seed"] == 1
    assert body["resume"] is False
    assert [m["name"] for m in body["set"]["mods"]] == ["@TraderPlus", "@Airdrop-Upgraded"]
    assert body["set"]["mods"][0]["url"].endswith(f"?id={TRADERPLUS}")
    assert [d["name"] for d in body["set"]["dependencies"]] == ["@CF"]
    assert (body["set"]["dayz_version"], body["set"]["tested_on"]) == ("1.30", "2026-11-10")


def test_no_tested_set_is_an_answer_and_not_an_error(app_and_config, parts, monkeypatch):
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", ())
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.get("/api/quick-setup/mods")
    assert resp.status_code == 200
    assert resp.json()["set"] is None


def test_the_roll_writes_nothing(app_and_config, parts, sample_config):
    with TestClient(app_and_config) as client:
        _login(client)
        client.get("/api/quick-setup/mods?seed=1")
    assert modlist.installed(sample_config) == []
    assert parts == []


def test_a_list_from_a_stopped_apply_offers_the_same_set(app_and_config, parts, monkeypatch, sample_config):
    monkeypatch.setattr(recommended_mods, "TESTED_SETS", (OTHER, TESTED))
    _list_mods(sample_config, ("@Airdrop-Upgraded", AIRDROP_UPGRADED))
    with TestClient(app_and_config) as client:
        _login(client)
        body = client.get("/api/quick-setup/mods?seed=1").json()
    assert body["resume"] is True
    assert {m["workshop_id"] for m in body["set"]["mods"]} == set(TESTED.mods)


def test_a_list_with_another_mod_names_it_and_does_not_resume(app_and_config, parts, sample_config):
    _list_mods(sample_config, ("@SomeMod", "123456"))
    with TestClient(app_and_config) as client:
        _login(client)
        body = client.get("/api/quick-setup/mods?seed=1").json()
    assert body["resume"] is False
    assert body["outside"] == ["@SomeMod"]


# --- refusals -----------------------------------------------------------------


def test_refuses_while_the_server_runs(app_and_config, parts, monkeypatch):
    monkeypatch.setattr(quick_setup.service_control, "is_server_running", lambda config: True)
    with TestClient(app_and_config) as client:
        _login(client)
        assert _apply(client).status_code == 409
    assert parts == []


def test_refuses_a_server_directory_it_cannot_create(app_and_config, parts, monkeypatch):
    monkeypatch.setattr(quick_setup, "server_dir_unwritable", lambda path: "Zedstead cannot create it.")
    with TestClient(app_and_config) as client:
        _login(client)
        resp = _apply(client)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Zedstead cannot create it."
    assert parts == []


def test_refuses_an_unknown_preset(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        assert client.post("/api/quick-setup/apply", json={"preset": "custom:mine"}).status_code == 400


def test_refuses_a_mod_outside_the_list(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = _apply(client, mod_ids=[TRADERPLUS, "999"])
    assert resp.status_code == 400
    assert "999" in resp.json()["detail"]


def test_refuses_a_list_that_holds_another_mod(app_and_config, parts, sample_config):
    _list_mods(sample_config, ("@SomeMod", "123456"))
    with TestClient(app_and_config) as client:
        _login(client)
        resp = _apply(client, mod_ids=list(TESTED.mods))
    assert resp.status_code == 409
    assert "@SomeMod" in resp.json()["detail"]


def test_vanilla_refuses_a_list_that_holds_mods_and_the_card_says_why(app_and_config, parts, sample_config):
    _list_mods(sample_config, ("@SomeMod", "123456"))
    with TestClient(app_and_config) as client:
        _login(client)
        resp = _apply(client)
        card = client.get("/api/quick-setup/mods?seed=1").json()
    assert resp.status_code == 409
    assert "@SomeMod" in resp.json()["detail"]
    assert card["vanilla_refusal"] == resp.json()["detail"]
    assert parts == []
    assert modlist.installed(sample_config) == ["@SomeMod"], "nothing is removed"


@pytest.fixture()
def mods_off(monkeypatch) -> list:
    """Stub the backup, the mission XML toggle and the unit rebuild of "Turn off these mods"."""
    from app.ops import backup_ops

    seen: list = []
    monkeypatch.setattr(quick_setup.service_control, "is_server_running", lambda config: False)
    monkeypatch.setattr(backup_ops, "quick_backup_problem", lambda config: None)

    def _backup(config, contents, note="", log=None):
        seen.append(("backup", note))
        return config.server_dir_path / "quick.zip"

    async def _regen(config):
        seen.append("service")
        return True

    monkeypatch.setattr(backup_ops, "create_backup", _backup)
    monkeypatch.setattr(quick_setup, "_toggle_mod_economy", lambda config, name, enable: seen.append(("xml", name, enable)))
    monkeypatch.setattr(quick_setup, "_regenerate_service", _regen)
    return seen


def test_turn_off_mods_backs_up_first_and_then_vanilla_applies(app_and_config, parts, mods_off, sample_config):
    """A server that loads mods could not use Vanilla at all. The button turns
    them off after a backup, keeps them installed, and Vanilla then applies."""
    _list_mods(sample_config, ("@SomeMod", "123456"), ("@Other", "654321"))
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, client.post("/api/quick-setup/mods-off").json()["job_id"])
        card = client.get("/api/quick-setup/mods?seed=1").json()
        applied = _apply(client)
    assert body["status"] == "success", body
    assert mods_off == [
        ("backup", quick_setup._MODS_OFF_NOTE),
        ("xml", "@SomeMod", False),
        ("xml", "@Other", False),
        "service",
    ]
    assert modlist.installed(sample_config) == ["@SomeMod", "@Other"], "nothing is removed"
    assert modlist.disabled(sample_config) == {"@SomeMod", "@Other"}
    assert card["vanilla_refusal"] == "" and applied.status_code == 200


def test_turn_on_again_turns_on_only_the_mods_it_turned_off(app_and_config, parts, mods_off, sample_config):
    """On the Mods page the way back was one click per mod. A mod that was off
    before stays off."""
    _list_mods(sample_config, ("@SomeMod", "123456"), ("@Other", "654321"), ("@WasOff", "111111"))
    modlist.set_enabled(sample_config, "@WasOff", False)
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, client.post("/api/quick-setup/mods-off").json()["job_id"])
        card = client.get("/api/quick-setup/mods?seed=1").json()
        mods_off.clear()
        body = _poll_job(client, client.post("/api/quick-setup/mods-on").json()["job_id"])
        after = client.get("/api/quick-setup/mods?seed=1").json()
        again = client.post("/api/quick-setup/mods-on")
    assert card["turned_off"] == ["@SomeMod", "@Other"]
    assert body["status"] == "success", body
    assert mods_off == [("xml", "@SomeMod", True), ("xml", "@Other", True), "service"]
    assert modlist.disabled(sample_config) == {"@WasOff"}
    assert after["turned_off"] == [] and again.status_code == 409
    assert not sample_config.paths.quick_setup_mods_off.exists()


def test_apply_ends_the_way_back(app_and_config, parts, mods_off, sample_config):
    _list_mods(sample_config, ("@SomeMod", "123456"))
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, client.post("/api/quick-setup/mods-off").json()["job_id"])
        assert sample_config.paths.quick_setup_mods_off.exists()
        body = _poll_job(client, _apply(client, start=False).json()["job_id"])
    assert body["status"] == "success", body
    assert not sample_config.paths.quick_setup_mods_off.exists()


def test_turn_off_mods_changes_nothing_when_the_backup_fails(app_and_config, parts, mods_off, monkeypatch, sample_config):
    from app.ops import backup_ops

    monkeypatch.setattr(backup_ops, "create_backup", lambda config, contents, note="", log=None: None)
    _list_mods(sample_config, ("@SomeMod", "123456"))
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, client.post("/api/quick-setup/mods-off").json()["job_id"])
    assert body["status"] == "failed" and "Backup" in body["error"], body
    assert modlist.disabled(sample_config) == set()


def test_turn_off_mods_skips_the_backup_when_there_is_no_player_data(app_and_config, parts, mods_off, monkeypatch, sample_config):
    from app.ops import backup_ops

    monkeypatch.setattr(backup_ops, "quick_backup_problem", lambda config: "Nothing to back up yet.")
    _list_mods(sample_config, ("@SomeMod", "123456"))
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, client.post("/api/quick-setup/mods-off").json()["job_id"])
    assert body["status"] == "success", body
    assert "backup" not in [p[0] for p in mods_off if isinstance(p, tuple)]
    assert modlist.disabled(sample_config) == {"@SomeMod"}


def test_turn_off_mods_refuses_a_running_server_and_an_empty_list(app_and_config, parts, mods_off, monkeypatch, sample_config):
    with TestClient(app_and_config) as client:
        _login(client)
        empty = client.post("/api/quick-setup/mods-off")
        _list_mods(sample_config, ("@SomeMod", "123456"))
        monkeypatch.setattr(quick_setup.service_control, "is_server_running", lambda config: True)
        running = client.post("/api/quick-setup/mods-off")
    assert empty.status_code == 409 and running.status_code == 409
    assert mods_off == []


def test_turn_off_mods_needs_the_admin_role():
    from app.web.permissions import required_role

    assert required_role("POST", "/api/quick-setup/mods-off") == "admin"


def test_vanilla_on_an_empty_list_has_no_refusal(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        assert client.get("/api/quick-setup/mods?seed=1").json()["vanilla_refusal"] == ""


def test_refuses_mods_that_are_not_a_tested_set(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        assert _apply(client, mod_ids=[AIRDROP_UPGRADED, RADZONE]).status_code == 400


def test_refuses_a_set_on_a_map_it_did_not_pass(app_and_config, parts, monkeypatch):
    monkeypatch.setattr(quick_setup, "_target_map", lambda config, chosen=None: "Livonia")
    with TestClient(app_and_config) as client:
        _login(client)
        assert _apply(client, mod_ids=list(TESTED.mods)).status_code == 400


def test_apply_needs_the_admin_role():
    from app.web.permissions import required_role

    assert required_role("POST", "/api/quick-setup/apply") == "admin"


# --- the job ------------------------------------------------------------------


def test_the_parts_run_in_order(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client, mod_ids=list(TESTED.mods)).json()["job_id"])
    assert body["status"] == "success", body
    assert parts == ["server", "mods", "preset", "start"]
    assert body["kind"] == "quick_setup"
    assert body["result"]["boot"] == {"outcome": "joinable"}


def test_a_start_that_failed_logs_a_cross_and_not_done(app_and_config, parts, monkeypatch):
    async def _start(config, before, emit=None):
        return {"outcome": "not_started", "detail": "Failed to start service"}

    monkeypatch.setattr(quick_setup.post_update, "start_and_check", _start)
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client).json()["job_id"])
    assert body["status"] == "success", body
    assert body["result"]["boot"]["outcome"] == "not_started"
    log = "\n".join(entry["line"] for entry in body["log"])
    assert "✗ Start and boot check: not started" in log
    assert "✓ Start and boot check: done" not in log


def test_vanilla_has_no_mods_part(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, _apply(client).json()["job_id"])
    assert parts == ["server", "preset", "start"]


def test_the_server_stays_stopped_without_the_start_switch(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client, start=False).json()["job_id"])
    assert body["status"] == "success", body
    assert parts == ["server", "preset"]
    assert body["result"]["boot"] is None


# --- the "Your server" card ---------------------------------------------------

CARD = {"hostname": " My server ", "password": "join", "password_admin": "admin1", "max_players": 40, "map": "Livonia"}


@pytest.fixture()
def card_parts(parts, monkeypatch) -> list:
    """Also stub the map switch and the serverDZ write, recording what they got."""

    async def _switch(job, config, map_id, template, **kw):
        parts.append(("map", map_id, template))

    def _save(config, values):
        parts.append(("cfg", values))

    monkeypatch.setattr(quick_setup, "_switch_vanilla_map", _switch)
    monkeypatch.setattr("app.maps.map_ops.get_active_map_name", lambda config: "Chernarus")
    monkeypatch.setattr("app.maps.map_ops.vanilla_mission", lambda config, name: "dayzOffline.enoch")
    monkeypatch.setattr("app.web.editors.server_forms.save_serverdz", _save)
    return parts


def test_the_card_switches_the_map_first_and_writes_its_answers_after_the_preset(app_and_config, card_parts):
    """The map comes first, since the mods and the preset write into the
    active mission. The answers come last: every built-in preset sets
    maxPlayers, and the player count the admin typed must win."""
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client, server=CARD, start=False).json()["job_id"])
    assert body["status"] == "success", body
    assert card_parts == [
        "server",
        ("map", "Livonia", "dayzOffline.enoch"),
        "preset",
        ("cfg", {"hostname": "My server", "password": "join", "passwordAdmin": "admin1", "maxPlayers": 40, "logAverageFps": 60}),
    ]


def test_the_card_keeps_the_map_the_server_runs(app_and_config, card_parts):
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, _apply(client, server={**CARD, "map": "Chernarus"}, start=False).json()["job_id"])
    assert [p for p in card_parts if p[0] == "map"] == []


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"hostname": "  "}, "Give the server a name."),
        ({"password_admin": ""}, "Set an in-game admin password."),
        ({"map": "Deer Isle"}, "cannot set up the map"),
        ({"max_players": 0}, "Max players"),
    ],
)
def test_the_card_refuses_an_answer_before_the_job_starts(app_and_config, card_parts, change, text):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = _apply(client, server={**CARD, **change})
    assert resp.status_code == 400 and text in resp.json()["detail"], resp.json()
    assert card_parts == []


def test_a_name_the_container_sets_is_left_out(app_and_config, card_parts, monkeypatch):
    monkeypatch.setattr(
        "app.web.editors.server_forms.env_locked_notes", lambda config: {"hostname": "Set by SERVER_NAME."}
    )
    with TestClient(app_and_config) as client:
        _login(client)
        mods = client.get("/api/quick-setup/mods").json()
        body = _poll_job(client, _apply(client, server={**CARD, "hostname": ""}, start=False).json()["job_id"])
    assert body["status"] == "success", body
    assert mods["locked"] == {"hostname": "Set by SERVER_NAME."}
    assert ("cfg", {"password": "join", "passwordAdmin": "admin1", "maxPlayers": 40, "logAverageFps": 60}) in card_parts


@pytest.mark.parametrize("own", ["0", "30"])
def test_the_card_keeps_an_fps_log_interval_the_cfg_sets(app_and_config, card_parts, sample_config, own):
    """Quick setup turns the FPS line on (night test 2026-11-22: a new server's
    FPS tile stayed empty), but an admin's own value, 0 included, stays."""
    from app.parsers.servercfg import write_cfg_value

    write_cfg_value(sample_config.server_cfg_path, "logAverageFps", own)
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, _apply(client, server=CARD, start=False).json()["job_id"])
    [(_, values)] = [p for p in card_parts if p[0] == "cfg"]
    assert "logAverageFps" not in values


def test_the_modded_card_follows_the_cards_map(app_and_config, parts):
    with TestClient(app_and_config) as client:
        _login(client)
        livonia = client.get("/api/quick-setup/mods?map=Livonia").json()
        chernarus = client.get("/api/quick-setup/mods?map=Chernarus").json()
        bad = client.get("/api/quick-setup/mods?map=Nowhere")
        refused = _apply(client, mod_ids=list(TESTED.mods), server=CARD)
    assert livonia["map"] == "Livonia" and livonia["set"] is None
    assert chernarus["set"] is not None and "Chernarus" in [m["name"] for m in chernarus["maps"]]
    assert bad.status_code == 400
    assert refused.status_code == 400 and "Livonia" in refused.json()["detail"]


def test_the_card_offers_only_maps_that_ship_with_the_server():
    assert set(quick_setup.quick_maps()) <= set(VANILLA_MAPS)
    assert all(VANILLA_MAPS[name]["mission"] for name in quick_setup.quick_maps())


def test_a_paid_dlc_map_says_so_and_a_free_one_does_not():
    """A rookie who picks Sakhal gets a server that a player without Frostline
    cannot join, so the card names the DLC beside the map."""
    by_name = {m["name"]: m for m in quick_setup._map_choices()}
    assert by_name["Sakhal"]["dlc"] == "Frostline" and "cannot join" in by_name["Sakhal"]["dlc_note"]
    assert by_name["Chernarus"]["dlc"] == "" and by_name["Livonia"]["dlc"] == ""
    assert all(bool(m["dlc"]) == bool(m["dlc_note"]) for m in by_name.values())


def test_an_installed_server_skips_the_install(app_and_config, parts, monkeypatch, sample_config):
    """A setup that stopped after the install runs again without a download."""
    monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: True)
    monkeypatch.setattr(quick_setup, "_target_map", lambda config: "Chernarus")
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, _apply(client).json()["job_id"])
    assert parts == ["preset", "start"]


# --- offered only until the server is set up ----------------------------------


def _state(client: TestClient) -> dict:
    return client.get("/api/quick-setup/state").json()


def test_a_finished_setup_is_no_longer_offered_and_refuses_a_second_run(app_and_config, parts, monkeypatch):
    with TestClient(app_and_config) as client:
        _login(client)
        assert _state(client)["offered"] is True
        body = _poll_job(client, _apply(client).json()["job_id"])
        assert body["status"] == "success", body
        monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: True)
        parts.clear()
        state = _state(client)
        second = _apply(client)
    assert state["offered"] is False and "Instances page" in state["reason"]
    assert second.status_code == 409 and second.json()["detail"] == state["reason"]
    assert parts == []


def test_a_stopped_setup_stays_offered_after_the_install(app_and_config, parts, monkeypatch):
    def _fail(config, preset):
        raise RuntimeError("no disk")

    monkeypatch.setattr(quick_setup, "apply_preset_to_mission", _fail)
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client).json()["job_id"])
        monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: True)
        state = _state(client)
    assert body["status"] == "failed"
    assert state["offered"] is True


def test_the_install_card_installs_only_and_keeps_the_setup_offered(app_and_config, parts, monkeypatch):
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/quick-setup/apply", json={"install_only": True})
        body = _poll_job(client, resp.json()["job_id"])
        monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: True)
        state = _state(client)
    assert body["status"] == "success", body
    assert body["kind"] == "update"
    assert parts == ["server"]
    assert state["offered"] is True, "the preset and the mods are still to come"


def test_the_install_card_refuses_a_folder_it_cannot_create(app_and_config, parts, monkeypatch):
    monkeypatch.setattr(quick_setup, "server_dir_unwritable", lambda path: "Zedstead cannot create it.")
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/quick-setup/apply", json={"install_only": True})
    assert resp.status_code == 409
    assert parts == []


def test_a_server_installed_by_another_button_stays_offered(app_and_config, parts, monkeypatch):
    """The Dashboard's and the Doctor's Install server write no marker, so the
    difficulty and the mods are still offered."""
    monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: True)
    with TestClient(app_and_config) as client:
        _login(client)
        assert _state(client)["offered"] is True


def test_a_finished_setup_is_offered_again_when_the_server_is_gone(app_and_config, parts, monkeypatch):
    with TestClient(app_and_config) as client:
        _login(client)
        _poll_job(client, _apply(client).json()["job_id"])
        monkeypatch.setattr("app.commands.doctor.server_is_installed", lambda config: False)
        assert _state(client)["offered"] is True


@pytest.mark.parametrize("stub, label", [
    ("run_mod_install", "Mods"),
    ("apply_preset_to_mission", "Difficulty preset"),
])
def test_a_failed_part_stops_the_job_and_is_named(app_and_config, parts, monkeypatch, stub, label):
    def _boom(*a, **k):
        raise ValueError("no disk")

    async def _boom_async(*a, **k):
        _boom()

    monkeypatch.setattr(quick_setup, stub, _boom_async if stub == "run_mod_install" else _boom)
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client, mod_ids=list(TESTED.mods)).json()["job_id"])
    assert body["status"] == "failed"
    assert label in body["error"] and "no disk" in body["error"]
    assert "start" not in parts


def test_a_failed_server_install_stops_the_job(app_and_config, parts, monkeypatch):
    class _Failed:
        status = "failed"
        error = "Steam says no"
        label = "update_server"

    async def _run_pipeline(steps, ctx):
        return [_Failed()], []

    monkeypatch.setattr(quick_setup.pipeline, "run_pipeline", _run_pipeline)
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client).json()["job_id"])
    assert body["status"] == "failed"
    assert "Server install" in body["error"] and "Steam says no" in body["error"]
    assert parts == []


def test_a_mod_that_did_not_install_stops_the_job(app_and_config, parts, monkeypatch):
    async def _install(job, state, **kw):
        return {"installed": [TRADERPLUS], "already": [], "skipped": ["@Airdrop-Upgraded"], "missing_addons": []}

    monkeypatch.setattr(quick_setup, "run_mod_install", _install)
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client, mod_ids=list(TESTED.mods)).json()["job_id"])
    assert body["status"] == "failed"
    assert "@Airdrop-Upgraded" in body["error"]
    assert "preset" not in parts


def test_a_stop_during_a_part_ends_the_job(app_and_config, parts, monkeypatch):
    async def _run_pipeline(steps, ctx):
        ctx.cancel_event.set()
        return [], []

    monkeypatch.setattr(quick_setup.pipeline, "run_pipeline", _run_pipeline)
    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client).json()["job_id"])
    assert body["status"] == "cancelled"
    assert parts == []


def test_a_second_apply_after_a_stop_finishes_the_set(app_and_config, parts, monkeypatch, sample_config):
    """The first attempt stops after the first of two mods. The new attempt is
    accepted, downloads only the second mod, and still merges the XML."""
    from app.web.routers import mods as mods_router
    from app.ops import pipeline as ops_pipeline

    _mock_install_ops(monkeypatch)
    monkeypatch.setattr(quick_setup, "run_mod_install", mods_router.run_mod_install)
    downloads: list[str] = []
    merges: list[int] = []
    stop_after_first = [True]

    async def _download(runner, config, mod_id, mod_name, max_retries=5):
        downloads.append(mod_name)
        if stop_after_first[0]:
            runner.job.cancel_event.set()
        return True

    monkeypatch.setattr(mods_router, "download_mod_async", _download)
    monkeypatch.setattr(ops_pipeline, "_merge_xml_sync", lambda ctx: merges.append(1) or "")

    with TestClient(app_and_config) as client:
        _login(client)
        first = _poll_job(client, _apply(client, mod_ids=list(TESTED.mods)).json()["job_id"])
        assert first["status"] == "cancelled"
        assert modlist.installed(sample_config) == ["@TraderPlus"]

        offer = client.get("/api/quick-setup/mods?seed=1").json()
        assert offer["resume"] is True

        stop_after_first[0] = False
        downloads.clear()
        merges.clear()
        resp = _apply(client, mod_ids=list(TESTED.mods))
        assert resp.status_code == 200
        second = _poll_job(client, resp.json()["job_id"])

    assert second["status"] == "success", second
    assert "@TraderPlus" not in downloads
    assert "@Airdrop-Upgraded" in downloads
    assert merges == [1]
    assert second["result"]["mods"]["already"] == ["@TraderPlus"]
    assert set(modlist.installed(sample_config)) >= {"@TraderPlus", "@Airdrop-Upgraded"}


def test_a_second_apply_with_every_mod_present_still_finishes_the_steps(app_and_config, parts, monkeypatch, sample_config):
    """A stop after the downloads leaves a full list. The XML merge must still run."""
    from app.web.routers import mods as mods_router
    from app.ops import pipeline as ops_pipeline

    _mock_install_ops(monkeypatch)
    monkeypatch.setattr(quick_setup, "run_mod_install", mods_router.run_mod_install)
    merges: list[int] = []
    downloads: list[str] = []

    async def _download(runner, config, mod_id, mod_name, max_retries=5):
        downloads.append(mod_name)
        return True

    monkeypatch.setattr(mods_router, "download_mod_async", _download)
    monkeypatch.setattr(ops_pipeline, "_merge_xml_sync", lambda ctx: merges.append(1) or "")
    _list_mods(sample_config, ("@CF", "1559212036"), ("@TraderPlus", TRADERPLUS), ("@Airdrop-Upgraded", AIRDROP_UPGRADED))

    with TestClient(app_and_config) as client:
        _login(client)
        body = _poll_job(client, _apply(client, mod_ids=list(TESTED.mods)).json()["job_id"])

    assert body["status"] == "success", body
    assert downloads == []
    assert merges == [1]


def test_the_shared_install_still_reinstalls_for_the_mods_page(app_and_config, monkeypatch, sample_config):
    """The Mods page treats a reinstall as an update, so its route never skips."""
    from app.web.routers import mods as mods_router

    _mock_install_ops(monkeypatch)
    downloads: list[str] = []

    async def _download(runner, config, mod_id, mod_name, max_retries=5):
        downloads.append(mod_name)
        return True

    monkeypatch.setattr(mods_router, "download_mod_async", _download)
    _list_mods(sample_config, ("@CF", "1559212036"))

    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post("/api/mods/install", json={"names": ["CF"]})
        body = _poll_job(client, resp.json()["job_id"])

    assert body["status"] == "success", body
    assert downloads == ["@CF"]
