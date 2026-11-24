"""Dynamic AI's map layers (`app/spawn_editor/dynamic_ai_state.py`, plans/47 D).

Points, locations and audio locations from the mod's SpatialSettings.json: moved
and resized on the map, and new entries only as copies (D3). The fixture
documents are built from key names (`tests/test_dynamic_ai.py`); nothing from
the mod's own files is copied.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ops import dynamic_ai
from app.spawn_editor.converters import game_to_latlng
from tests.test_dynamic_ai import MOD, _location, _point, _settings
from tests.conftest import mods_lines, write_test_mods

PASSWORD = "testpass123"
TERRAIN = "chernarusplus"
API = "/api/map-editor"


def _latlng(x: float, z: float) -> dict:
    lat, lng = game_to_latlng(x, z, TERRAIN)
    return {"lat": lat, "lng": lng}


def _login(config) -> TestClient:
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(config))
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


def _audio(name="Shots") -> dict:
    entry = _location(name)
    entry["Spatial_Sensitivity"] = 3.0
    return entry


def _placed_location() -> dict:
    entry = _location("Tower")
    entry["Spatial_TriggerPosition"] = [5000.0, 120.5, 6000.0]
    entry["Spatial_SpawnPosition"] = [[5010.0, 130.0, 6005.0], [4990.0, 140.25, 5995.0]]
    return entry


DOC = _settings(
    Points_Enabled=1,
    Point=[
        _point("Airfield", Spatial_Position=[4500.0, 12.5, 10200.0], Spatial_Radius=150.0),
        _point("Unset"),
        _point("Camp", Spatial_Safe=1, Spatial_Position=[3000.0, 5.0, 3000.0]),
    ],
    Location=[_placed_location(), _location("Placeholder")],
    Audio=[_audio()],
)


def _install(cfg, doc=DOC) -> Path:
    write_test_mods(cfg, mods_lines(f"@CF\n{MOD}\n"))
    (cfg.server_dir_path / MOD).mkdir(exist_ok=True)
    folder = dynamic_ai.settings_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SpatialSettings.json"
    path.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    return path


@pytest.fixture()
def settings_file(sample_config, tmp_mission_dir) -> Path:
    return _install(sample_config)


@pytest.fixture()
def client(sample_config, settings_file):
    return _login(sample_config)


def _layers(client) -> dict:
    return client.get(f"{API}/dynamic-ai").json()


def _doc(path: Path) -> dict:
    return json.loads(path.read_text())


# ── reading ───────────────────────────────────────────────────────


def test_points_carry_their_circle_and_whether_they_are_safe(client):
    points = _layers(client)["points"]
    assert [p["name"] for p in points] == ["Airfield", "Unset", "Camp"]
    airfield, unset, camp = points
    assert (airfield["game_x"], airfield["game_y"], airfield["game_z"]) == (4500.0, 12.5, 10200.0)
    assert airfield["radius"] == 150.0 and airfield["safe"] is False
    assert airfield["lat"] == pytest.approx(_latlng(4500.0, 10200.0)["lat"], abs=1e-5)
    assert camp["safe"] is True
    assert airfield["faction"] == "East" and airfield["max_count"] == 2


def test_the_mods_placeholder_position_is_flagged_not_hidden(client):
    """A fresh install puts every entry at [0, 1, 0]. It is drawn where the
    file says, and marked so the map can say why it sits in the corner."""
    body = _layers(client)
    assert [p["unplaced"] for p in body["points"]] == [False, True, False]
    assert [loc["unplaced"] for loc in body["locations"]] == [False, True]


def test_locations_carry_the_trigger_and_every_spawn_position(client):
    tower = _layers(client)["locations"][0]
    assert tower["radius"] == 50.0
    assert (tower["trigger"]["game_x"], tower["trigger"]["game_z"]) == (5000.0, 6000.0)
    assert [(s["index"], s["game_x"], s["game_y"]) for s in tower["spawns"]] == [
        (0, 5010.0, 130.0),
        (1, 4990.0, 140.25),
    ]
    assert _layers(client)["audio"][0]["name"] == "Shots"


def test_the_modes_say_which_lists_are_switched_on(client):
    assert _layers(client)["modes"] == {"points": 1.0, "locations": 0.0, "audio": 0.0}


def test_map_info_says_whether_dynamic_ai_is_there(client):
    assert client.get(f"{API}/map-info").json()["dynamic_ai"] is True


def test_not_installed_answers_empty_and_refuses_writes(sample_config, tmp_mission_dir):
    """Uninstall keeps profiles/ExpansionMod/AI/Spatial (live data); the
    entries are not active, so nothing is drawn from the leftover file."""
    path = _install(sample_config)
    write_test_mods(sample_config, ['@CF'])
    client = _login(sample_config)
    assert _layers(client)["points"] == []
    assert client.get(f"{API}/map-info").json()["dynamic_ai"] is False
    before = path.read_bytes()
    resp = client.put(f"{API}/dynamic-ai/points/0", json={"radius": 5})
    assert resp.status_code == 404
    assert path.read_bytes() == before


def test_installed_after_the_editor_opened_shows_on_a_reload(sample_config, tmp_mission_dir):
    client = _login(sample_config)
    assert client.get(f"{API}/map-info").json()["dynamic_ai"] is False
    _install(sample_config)
    assert client.post(f"{API}/reload").status_code == 200
    assert len(_layers(client)["points"]) == 3


# ── moving and resizing ───────────────────────────────────────────


def test_moving_a_point_writes_its_x_and_z_and_nothing_else(client, settings_file):
    resp = client.put(f"{API}/dynamic-ai/points/0", json=_latlng(4600.4, 10100.6))
    assert resp.status_code == 200
    doc = _doc(settings_file)
    moved = doc["Point"][0]
    x, y, z = moved["Spatial_Position"]
    assert (x, y, z) == (pytest.approx(4600.4, abs=0.02), 12.5, pytest.approx(10100.6, abs=0.02))
    expected = {k: v for k, v in DOC["Point"][0].items() if k != "Spatial_Position"}
    assert {k: v for k, v in moved.items() if k != "Spatial_Position"} == expected
    assert doc["Point"][1:] == DOC["Point"][1:]
    assert doc["Location"] == DOC["Location"] and doc["Group"] == DOC["Group"]
    assert doc["Version"] == 21


def test_resizing_a_point_keeps_the_float_type(client, settings_file):
    client.put(f"{API}/dynamic-ai/points/0", json={"radius": 199.456})
    assert _doc(settings_file)["Point"][0]["Spatial_Radius"] == 199.46


def test_moving_a_trigger_leaves_its_spawn_positions(client, settings_file):
    client.put(f"{API}/dynamic-ai/locations/0", json=_latlng(5500.0, 6500.0))
    tower = _doc(settings_file)["Location"][0]
    assert tower["Spatial_TriggerPosition"][1] == 120.5
    assert tower["Spatial_TriggerPosition"][0] == pytest.approx(5500.0, abs=0.02)
    assert tower["Spatial_SpawnPosition"] == DOC["Location"][0]["Spatial_SpawnPosition"]


def test_move_here_carries_the_spawn_positions_with_their_heights(client, settings_file):
    body = {**_latlng(5100.0, 6200.0), "carry_spawns": True}
    assert client.put(f"{API}/dynamic-ai/locations/0", json=body).status_code == 200
    spawns = _doc(settings_file)["Location"][0]["Spatial_SpawnPosition"]
    # Same layout around the trigger (+100, +200), heights as they were.
    assert spawns[0] == [pytest.approx(5110.0, abs=0.05), 130.0, pytest.approx(6205.0, abs=0.05)]
    assert spawns[1] == [pytest.approx(5090.0, abs=0.05), 140.25, pytest.approx(6195.0, abs=0.05)]


def test_a_spawn_position_carried_off_the_map_is_refused(client, settings_file):
    """The clicked point is on the map; the spawn position 10 m west of the
    trigger is not. Found in the browser: after a trigger-only move, a ticked
    Move here… put a spawn position at x -2804."""
    before = settings_file.read_bytes()
    body = {**_latlng(5.0, 6000.0), "carry_spawns": True}
    resp = client.put(f"{API}/dynamic-ai/locations/0", json=body)
    assert resp.status_code == 400
    assert "Spawn position 2" in resp.json()["detail"]
    assert settings_file.read_bytes() == before


def test_a_copy_whose_spawn_position_would_leave_the_map_is_refused(client, settings_file):
    before = settings_file.read_bytes()
    resp = client.post(f"{API}/dynamic-ai/locations/0/duplicate", json=_latlng(5.0, 6000.0))
    assert resp.status_code == 400
    assert settings_file.read_bytes() == before


def test_moving_one_spawn_position(client, settings_file):
    body = {**_latlng(4980.0, 5990.0), "spawn": 1}
    assert client.put(f"{API}/dynamic-ai/locations/0", json=body).status_code == 200
    tower = _doc(settings_file)["Location"][0]
    assert tower["Spatial_SpawnPosition"][0] == [5010.0, 130.0, 6005.0]
    assert tower["Spatial_SpawnPosition"][1][1] == 140.25
    assert tower["Spatial_TriggerPosition"] == [5000.0, 120.5, 6000.0]


def test_resizing_an_audio_trigger(client, settings_file):
    client.put(f"{API}/dynamic-ai/audio/0", json={"radius": 80})
    assert _doc(settings_file)["Audio"][0]["Spatial_TriggerRadius"] == 80.0


@pytest.mark.parametrize(
    "path, body",
    [
        ("points/0", {"spawn": 0, "lat": 1, "lng": 1}),
        ("locations/0", {"spawn": 5, "lat": 1, "lng": 1}),
        ("locations/0", {"spawn": 0}),
        ("locations/0", {"lat": "north", "lng": 1}),
        ("points/0", {"radius": "big"}),
    ],
)
def test_a_bad_request_is_refused_and_changes_nothing(client, settings_file, path, body):
    before = settings_file.read_bytes()
    assert client.put(f"{API}/dynamic-ai/{path}", json=body).status_code == 400
    assert settings_file.read_bytes() == before
    # The in-memory document is untouched too, so the next write cannot carry it.
    client.put(f"{API}/dynamic-ai/audio/0", json={"radius": 60})
    doc = _doc(settings_file)
    assert doc["Point"] == DOC["Point"] and doc["Location"] == DOC["Location"]


def test_unknown_lists_and_entries_are_404(client):
    assert client.put(f"{API}/dynamic-ai/groups/0", json={"radius": 5}).status_code == 404
    assert client.put(f"{API}/dynamic-ai/points/9", json={"radius": 5}).status_code == 404
    assert client.post(f"{API}/dynamic-ai/audio/3/duplicate", json=_latlng(1, 1)).status_code == 404


# ── duplicating (D3) ──────────────────────────────────────────────


def test_a_duplicate_is_the_whole_entry_at_the_new_place(client, settings_file):
    resp = client.post(f"{API}/dynamic-ai/points/2/duplicate", json=_latlng(3500.0, 3600.0))
    assert resp.json() == {"section": "points", "index": 3}
    doc = _doc(settings_file)
    copy_ = doc["Point"][3]
    assert copy_["Spatial_Name"] == "Camp" and copy_["Spatial_Safe"] == 1
    assert copy_["Spatial_Position"][1] == 5.0
    assert copy_["Spatial_Position"][0] == pytest.approx(3500.0, abs=0.02)
    assert doc["Point"][:3] == DOC["Point"]


def test_a_duplicated_location_keeps_its_spawn_layout_and_takes_a_name(client, settings_file):
    body = {**_latlng(6000.0, 7000.0), "name": "Tower 2"}
    client.post(f"{API}/dynamic-ai/locations/0/duplicate", json=body)
    doc = _doc(settings_file)
    copy_ = doc["Location"][2]
    assert copy_["Spatial_Name"] == "Tower 2"
    assert copy_["Spatial_SpawnPosition"][0][0] == pytest.approx(6010.0, abs=0.05)
    assert copy_["Spatial_SpawnPosition"][0][2] == pytest.approx(7005.0, abs=0.05)
    assert doc["Location"][0] == DOC["Location"][0]


def test_a_duplicate_needs_a_position(client, settings_file):
    before = settings_file.read_bytes()
    assert client.post(f"{API}/dynamic-ai/points/0/duplicate", json={}).status_code == 400
    assert settings_file.read_bytes() == before


# ── what it never touches ─────────────────────────────────────────


def test_the_player_database_and_the_mod_folder_are_never_written(sample_config, client):
    folder = dynamic_ai.settings_dir(sample_config)
    players = folder / "SpatialPlayerSettings.json"
    players.write_text('{"Version": 1}', encoding="utf-8")
    mod_dir = sample_config.server_dir_path / MOD
    before = {p: p.read_bytes() for p in mod_dir.rglob("*") if p.is_file()}
    client.put(f"{API}/dynamic-ai/points/0", json=_latlng(100.0, 100.0))
    client.post(f"{API}/dynamic-ai/points/0/duplicate", json=_latlng(200.0, 200.0))
    assert players.read_text() == '{"Version": 1}'
    assert {p: p.read_bytes() for p in mod_dir.rglob("*") if p.is_file()} == before


def test_a_write_keeps_numbers_it_did_not_change_as_the_file_had_them(
    sample_config, tmp_mission_dir
):
    """The mod writes float32 values (0.6600000262260437); a move must not
    reformat them."""
    raw = json.dumps(DOC, indent=4).replace('"Spatial_MaxAccuracy": 0.66', '"Spatial_MaxAccuracy": 0.6600000262260437')
    path = _install(sample_config)
    path.write_text(raw, encoding="utf-8")
    client = _login(sample_config)
    client.put(f"{API}/dynamic-ai/points/0", json={"radius": 160})
    assert path.read_text().count("0.6600000262260437") == raw.count("0.6600000262260437")
