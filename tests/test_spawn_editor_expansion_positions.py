"""Expansion's position lists inside settings files, as map layers (TODO H4).

`app/spawn_editor/expansion_position_state.py` + its routes. The fixtures have
the shapes Expansion writes into a mission (measured in the test container on
2026 Q3): a vehicle spawn is a position plus an orientation, a server marker
carries a signed ARGB colour and a UID, a build zone a centre, radius and item
list. The values are made up; nothing from Expansion's files is copied
(licence, plans/65).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.spawn_editor.converters import game_to_latlng

PASSWORD = "testpass123"
TERRAIN = "chernarusplus"
API = "/api/map-editor/expansion"

MARKET = {
    "m_Version": 16,
    "MarketSystemEnabled": 1,
    "MaxVehicleDistanceToTrader": 150.0,
    "MaxLargeVehicleDistanceToTrader": 600.0,
    "LandSpawnPositions": [
        {"Position": [8341.2001953125, 162.0, 5917.60009765625], "Orientation": [118.0, 0.0, 0.0]},
        {"Position": [8350.0, 162.5, 5930.0], "Orientation": [118.0, 0.0, 0.0]},
    ],
    "AirSpawnPositions": [
        {"Position": [8512.2998046875, 170.0, 6044.7998046875], "Orientation": [-63.70000076293945, 0.0, 0.0]}
    ],
    "WaterSpawnPositions": [{"Position": [2412.0, 0.0, 2187.0], "Orientation": [0.0, 0.0, 0.0]}],
    "TrainSpawnPositions": [],
    "VehicleKeys": ["ExpansionCarKey"],
}

MAP = {
    "m_Version": 7,
    "EnableServerMarkers": 1,
    "ServerMarkers": [
        {
            "m_UID": "ServerMarker_Test_Camp",
            "m_Visibility": 6,
            "m_Is3D": 1,
            "m_Text": "Camp - North ridge",
            "m_IconName": "Trader",
            "m_Color": -13330213,
            "m_Position": [8341.0, 162.0, 5917.0],
            "m_Locked": 0,
            "m_Persist": 1,
        },
        {
            "m_UID": "ServerMarker_Test_Dock",
            "m_Visibility": 4,
            "m_Is3D": 0,
            "m_Text": "Dock - South bay",
            "m_IconName": "Boat",
            "m_Color": -13330213,
            "m_Position": [2412.0, 5.0, 2187.0],
            "m_Locked": 0,
            "m_Persist": 1,
        },
    ],
}

BASEBUILDING = {
    "m_Version": 5,
    "ZonesAreNoBuildZones": 1,
    "Zones": [
        {
            "Name": "North ridge camp",
            "Center": [8341.0, 162.0, 5917.0],
            "Radius": 800.0,
            "Items": ["Fireplace", "LandMineTrap", "BearTrap"],
            "IsWhitelist": 1,
            "CustomMessage": "",
        }
    ],
}


def _latlng(x: float, z: float) -> dict:
    lat, lng = game_to_latlng(x, z, TERRAIN)
    return {"lat": lat, "lng": lng}


def _settings(mission: Path) -> Path:
    return mission / "expansion" / "settings"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")


def _doc(mission: Path, name: str) -> dict:
    return json.loads((_settings(mission) / name).read_text())


def _client(config) -> TestClient:
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(config))
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture()
def mission(tmp_mission_dir: Path) -> Path:
    _write(_settings(tmp_mission_dir) / "MarketSettings.json", MARKET)
    _write(_settings(tmp_mission_dir) / "MapSettings.json", MAP)
    _write(_settings(tmp_mission_dir) / "BaseBuildingSettings.json", BASEBUILDING)
    return tmp_mission_dir


@pytest.fixture()
def client(sample_config, mission):
    return _client(sample_config)


# ── market vehicle spawns ─────────────────────────────────────────


def test_market_spawns_carry_every_kind_and_the_trader_distance(client):
    body = client.get(f"{API}/market-spawns").json()
    assert [len(body[k]) for k in ("land", "air", "water", "train")] == [2, 1, 1, 0]
    land = body["land"][0]
    assert land["index"] == 0 and land["heading"] == 118.0
    assert land["game_x"] == pytest.approx(8341.2) and land["game_y"] == 162.0
    assert body["air"][0]["heading"] == pytest.approx(-63.7)
    assert body["max_distance"] == 150.0 and body["max_large_distance"] == 600.0
    assert body["enabled"] is True


def test_moving_a_vehicle_spawn_keeps_its_height_and_heading(client, mission):
    """Land vehicles are traced down from the stored position, so a move that
    guessed a height could bury the spawn; the height stays until typed."""
    assert client.put(f"{API}/market-spawns/land/0", json=_latlng(11800.0, 12400.0)).status_code == 200
    doc = _doc(mission, "MarketSettings.json")
    moved = doc["LandSpawnPositions"][0]
    assert moved["Position"][0] == pytest.approx(11800.0, abs=0.02)
    assert moved["Position"][2] == pytest.approx(12400.0, abs=0.02)
    assert moved["Position"][1] == 162.0
    assert moved["Orientation"] == [118.0, 0.0, 0.0]
    assert doc["LandSpawnPositions"][1] == MARKET["LandSpawnPositions"][1]
    assert doc["VehicleKeys"] == MARKET["VehicleKeys"]


def test_height_and_heading_are_typed_in_the_popup(client, mission):
    body = {"game_y": 150.25, "orientation": 90}
    assert client.put(f"{API}/market-spawns/air/0", json=body).status_code == 200
    entry = _doc(mission, "MarketSettings.json")["AirSpawnPositions"][0]
    assert entry["Position"] == [8512.2998046875, 150.25, 6044.7998046875]
    assert entry["Orientation"] == [90.0, 0.0, 0.0]


def test_a_heading_sent_back_as_shown_keeps_the_files_value(client, mission):
    """The popup shows -63.7; saving it untouched must not rewrite -63.7000007…"""
    client.put(f"{API}/market-spawns/air/0", json={"orientation": -63.7, "game_y": 170})
    entry = _doc(mission, "MarketSettings.json")["AirSpawnPositions"][0]
    assert entry == MARKET["AirSpawnPositions"][0]


def test_copying_a_vehicle_spawn_keeps_its_heading_and_height(client, mission):
    resp = client.post(f"{API}/market-spawns/water/0/duplicate", json=_latlng(1800.0, 2000.0))
    assert resp.json() == {"list": "water", "index": 1}
    water = _doc(mission, "MarketSettings.json")["WaterSpawnPositions"]
    assert water[0] == MARKET["WaterSpawnPositions"][0]
    assert water[1]["Position"][1] == 0.0 and water[1]["Orientation"] == [0.0, 0.0, 0.0]
    assert water[1]["Position"][0] == pytest.approx(1800.0, abs=0.02)


@pytest.mark.parametrize(
    "path",
    ["market-spawns/cars/0", "market-spawns/land/9", "market-spawns/train/0"],
)
def test_unknown_kinds_and_entries_are_404(client, path):
    assert client.put(f"{API}/{path}", json={"orientation": 5}).status_code == 404


def test_off_map_is_refused_before_anything_is_written(client, mission):
    before = (_settings(mission) / "MarketSettings.json").read_bytes()
    resp = client.put(f"{API}/market-spawns/land/0", json=_latlng(99999.0, 100.0))
    assert resp.status_code == 400
    assert (_settings(mission) / "MarketSettings.json").read_bytes() == before


# ── server map markers ────────────────────────────────────────────


def test_markers_carry_their_colour_and_where_they_show(client):
    camp, dock = client.get(f"{API}/map-markers").json()["markers"]
    assert camp["color"] == "#3498db" and camp["alpha"] == 255
    assert camp["on_map"] and camp["in_world"] and camp["is_3d"]
    assert dock["on_map"] and not dock["in_world"]
    assert camp["text"] == "Camp - North ridge" and camp["icon"] == "Trader"


def test_a_marker_edit_writes_text_icon_and_colour_and_keeps_the_alpha(client, mission):
    body = {"text": "Ridge camp", "icon": "Car", "color": "#112233"}
    assert client.put(f"{API}/map-markers/0", json=body).status_code == 200
    marker = _doc(mission, "MapSettings.json")["ServerMarkers"][0]
    assert marker["m_Text"] == "Ridge camp" and marker["m_IconName"] == "Car"
    assert marker["m_Color"] == -15654349  # 0xFF112233 as a signed int
    assert marker["m_Position"] == [8341.0, 162.0, 5917.0]


def test_an_unchanged_colour_is_not_rewritten(client, mission):
    client.put(f"{API}/map-markers/0", json={"color": "#3498DB", "text": "Camp - North ridge"})
    assert _doc(mission, "MapSettings.json")["ServerMarkers"][0] == MAP["ServerMarkers"][0]


@pytest.mark.parametrize("body", [{"text": "  "}, {"icon": ""}, {"color": "green"}, {"color": 5}])
def test_a_bad_marker_edit_is_refused_and_changes_nothing(client, mission, body):
    before = (_settings(mission) / "MapSettings.json").read_bytes()
    assert client.put(f"{API}/map-markers/0", json=body).status_code == 400
    assert (_settings(mission) / "MapSettings.json").read_bytes() == before
    # Nothing half-applied in memory for the next write to carry.
    client.put(f"{API}/map-markers/1", json={"text": "Boats"})
    assert _doc(mission, "MapSettings.json")["ServerMarkers"][0] == MAP["ServerMarkers"][0]


def test_a_copied_marker_gets_its_own_uid(client, mission):
    client.post(f"{API}/map-markers/0/duplicate", json=_latlng(5000.0, 5000.0))
    client.post(f"{API}/map-markers/0/duplicate", json=_latlng(5100.0, 5000.0))
    uids = [m["m_UID"] for m in _doc(mission, "MapSettings.json")["ServerMarkers"]]
    assert uids == [
        "ServerMarker_Test_Camp",
        "ServerMarker_Test_Dock",
        "ServerMarker_Test_Camp_2",
        "ServerMarker_Test_Camp_3",
    ]


# ── base-building zones ───────────────────────────────────────────


def test_zones_say_what_their_list_means(client):
    body = client.get(f"{API}/build-zones").json()
    (zone,) = body["zones"]
    assert zone["whitelist"] is True and zone["items"] == ["Fireplace", "LandMineTrap", "BearTrap"]
    assert zone["radius"] == 800.0 and zone["game_y"] == 162.0
    assert body["no_build_zones"] is True


def test_a_zone_moves_resizes_and_renames(client, mission):
    body = {**_latlng(11000.0, 12000.0), "radius": 250, "name": "North camp"}
    assert client.put(f"{API}/build-zones/0", json=body).status_code == 200
    zone = _doc(mission, "BaseBuildingSettings.json")["Zones"][0]
    assert zone["Name"] == "North camp" and zone["Radius"] == 250
    assert zone["Center"][1] == 162.0  # the check is 3D; the height is only typed
    assert zone["Items"] == BASEBUILDING["Zones"][0]["Items"]


def test_a_zone_needs_a_radius_and_a_name(client, mission):
    before = (_settings(mission) / "BaseBuildingSettings.json").read_bytes()
    assert client.put(f"{API}/build-zones/0", json={"radius": 0}).status_code == 400
    assert client.put(f"{API}/build-zones/0", json={"name": ""}).status_code == 400
    assert (_settings(mission) / "BaseBuildingSettings.json").read_bytes() == before


def test_a_copied_zone_keeps_its_items_and_takes_a_name(client, mission):
    body = {**_latlng(3000.0, 3000.0), "name": "South camp"}
    assert client.post(f"{API}/build-zones/0/duplicate", json=body).json()["index"] == 1
    copy_ = _doc(mission, "BaseBuildingSettings.json")["Zones"][1]
    assert copy_["Name"] == "South camp" and copy_["IsWhitelist"] == 1
    assert copy_["Items"] == BASEBUILDING["Zones"][0]["Items"]


def test_a_copy_needs_a_position(client):
    assert client.post(f"{API}/build-zones/0/duplicate", json={"name": "X"}).status_code == 400


# ── wiring ────────────────────────────────────────────────────────


def test_missing_files_answer_empty_and_refuse_writes(sample_config, tmp_mission_dir):
    client = _client(sample_config)
    assert client.get(f"{API}/map-markers").json()["markers"] == []
    assert client.get(f"{API}/build-zones").json()["zones"] == []
    resp = client.put(f"{API}/map-markers/0", json={"text": "X"})
    assert resp.status_code == 404 and "MapSettings.json" in resp.json()["detail"]
    assert client.get("/api/map-editor/map-info").json()["expansion"] is False


def test_any_of_the_three_files_counts_as_expansion_data(sample_config, tmp_mission_dir):
    _write(_settings(tmp_mission_dir) / "BaseBuildingSettings.json", BASEBUILDING)
    assert _client(sample_config).get("/api/map-editor/map-info").json()["expansion"] is True


def test_the_watcher_routes_the_three_files():
    from app.spawn_editor.watcher import expansion_reloads

    states = {"market": "MK", "map_marker": "MM", "build_zone": "BZ"}
    settings = Path("/srv/mpmissions/dayzOffline.chernarusplus/expansion/settings")
    assert expansion_reloads({settings / "MarketSettings.json"}, states) == ["MK"]
    assert expansion_reloads({settings / "MapSettings.json"}, states) == ["MM"]
    assert expansion_reloads({settings / "BaseBuildingSettings.json"}, states) == ["BZ"]


@pytest.mark.parametrize(
    "kind",
    ["expansion_market_spawns_changed", "expansion_map_markers_changed", "expansion_build_zones_changed"],
)
def test_their_broadcasts_reach_the_browser(kind):
    from app.spawn_editor.state import to_web_message

    assert to_web_message({"type": kind}) == {"type": f"mapeditor_{kind}"}
