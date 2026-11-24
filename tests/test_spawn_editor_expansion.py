"""Expansion's map-editor layers (app/spawn_editor/expansion_state.py + routes).

Shapes are the measured ones (plans/36): safe zones in three lists, one file per
trader zone with a `Stock` table the editor must never carry, spawn positions
grouped by location, and two kinds of mission file sharing one folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"

SAFEZONES = {
    "m_Version": 11,
    "Enabled": 1,
    "CircleZones": [{"Center": [3728.27, 403.0, 6003.6], "Radius": 500.0}],
    "CylinderZones": [{"Center": [1000.0, 5.0, 2000.0], "Radius": 100.0, "Height": 40.0}],
    "PolygonZones": [
        {
            "Positions": [
                [12288.9, 142.4, 12804.4],
                [12068.4, 139.8, 12923.4],
                [11680.6, 141.1, 12650.6],
            ]
        }
    ],
    "ItemLifetimeInSafeZone": 900.0,
}

TRADER_ZONE = {
    "m_Version": 6,
    "m_DisplayName": "Balota Aircrafts Trading Zone",
    "Position": [4971.0, 6.0, 2437.0],
    "Radius": 100.0,
    "BuyPricePercent": 100.0,
    "SellPricePercent": -1.0,
    # The real file carries up to 1932 of these; two is enough to prove they
    # never reach the payload and always survive a write.
    "Stock": {"Expansion_Mi8_Wreck": 1, "Expansion_UH1H": 2},
}

SPAWNS = {
    "m_Version": 12,
    "EnableSpawnSelection": 1,
    "SpawnLocations": [
        {
            "Name": "Svetloyarsk",
            "Positions": [[14273.2, 2.4, 13053.3], [14407.3, 2.0, 13253.0]],
            "UseCooldown": 1,
        }
    ],
    "StartingGear": {"Primary": []},
}

AIRDROP = {
    "Enabled": 1,
    "Weight": 294.0,
    "MissionName": "Random_Balota",
    "DropLocation": {"x": 5043.0, "z": 2505.0, "Name": "Balota", "Radius": 100.0},
}

CONTAMINATED = {
    "Enabled": 1,
    "Weight": 100.0,
    "MissionName": "Settlement_BelayaPolyana",
    "Data": {"Pos": [14166.0, 0.0, 14968.0], "Radius": 200.0, "PosHeight": 26.0},
}

UNKNOWN_MISSION = {"Enabled": 1, "MissionName": "SomethingNew"}


AI_PATROLS = {
    "m_Version": 32,
    "Patrols": [
        {
            "Name": "NWAF Guards",
            "Faction": "Mercenaries",
            "Formation": "Wall",
            "Loadout": "MercenaryLoadout",
            "NumberOfAI": 4,
            "Behaviour": "LOOP",
            "Speed": "JOG",
            "Chance": 0.7,
            "Waypoints": [
                [4700.0, 290.0, 10300.0],
                [4800.0, 291.0, 10400.0],
                [4900.0, 292.0, 10500.0],
            ],
        },
        {
            # An object patrol: it spawns wherever objects of this class are,
            # so there is no place on the map to draw it.
            "Name": "Heli crash guards",
            "ObjectClassName": "Wreck_Mi8_Crashed",
            "NumberOfAI": 2,
            "Waypoints": [],
        },
    ],
}

AI_LOCATIONS = {
    "m_Version": 4,
    "RoamingLocations": [
        {
            "Name": "Kamenka",
            "Position": [1800.0, 0.0, 2200.0],
            "Radius": 200.0,
            "Type": "Village",
            "Enabled": 1,
        },
        {
            "Name": "Balota",
            "Position": [4500.0, 0.0, 2500.0],
            "Radius": 300.0,
            "Type": "Military",
            "Enabled": 0,
        },
    ],
    "ExcludedRoamingBuildings": ["Land_Factory_Small"],
    "NoGoAreas": [
        {"Name": "Trader", "Position": [4971.0, 6.0, 2437.0], "Radius": 150.0, "Height": 60.0},
        # No position, no radius: not something the map can place.
        {"Name": "Broken"},
    ],
}


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return path


@pytest.fixture()
def expansion_mission(tmp_mission_dir: Path) -> Path:
    """A mission with every Expansion map-data file Expansion's boot writes."""
    exp = tmp_mission_dir / "expansion"
    _write(exp / "settings" / "SafeZoneSettings.json", SAFEZONES)
    _write(exp / "settings" / "SpawnSettings.json", SPAWNS)
    _write(exp / "traderzones" / "BalotaAircrafts.json", TRADER_ZONE)
    _write(exp / "missions" / "Airdrop_Random_Balota.json", AIRDROP)
    _write(exp / "missions" / "ContaminatedArea_Settlement_BelayaPolyana.json", CONTAMINATED)
    _write(exp / "missions" / "Something_New.json", UNKNOWN_MISSION)
    _write(exp / "settings" / "AIPatrolSettings.json", AI_PATROLS)
    _write(exp / "settings" / "AILocationSettings.json", AI_LOCATIONS)
    return tmp_mission_dir


@pytest.fixture()
def client(sample_config, expansion_mission):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    assert tclient.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return tclient


@pytest.fixture()
def bare_client(sample_config, tmp_mission_dir):
    """A mission Expansion has never written into."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


# ── map-info flag ─────────────────────────────────────────────────


def test_map_info_reports_expansion_data(client):
    assert client.get("/api/map-editor/map-info").json()["expansion"] is True


def test_map_info_reports_no_expansion_data_before_the_first_boot(bare_client):
    # Its own test: the two fixtures share one mission, so asking for both in
    # one test would seed the files the "bare" case is about.
    assert bare_client.get("/api/map-editor/map-info").json()["expansion"] is False


# ── safe zones ────────────────────────────────────────────────────


def test_safezones_serialize_all_three_shapes(client):
    body = client.get("/api/map-editor/expansion/safezones").json()

    (circle,) = body["circles"]
    assert circle["kind"] == "circle" and circle["index"] == 0
    assert circle["radius"] == 500.0
    assert (circle["game_x"], circle["game_z"]) == (3728.27, 6003.6)
    assert isinstance(circle["lat"], float) and isinstance(circle["lng"], float)

    (cylinder,) = body["cylinders"]
    assert cylinder["height"] == 40.0

    (polygon,) = body["polygons"]
    assert len(polygon["points"]) == 3
    assert polygon["points"][0]["game_x"] == 12288.9
    assert body["enabled"] is True


def test_dragging_a_circle_keeps_its_height(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "SafeZoneSettings.json"
    resp = client.put(
        "/api/map-editor/expansion/safezones/circle/0", json={"game_x": 100, "game_z": 200}
    )
    assert resp.status_code == 200

    zone = _json(path)["CircleZones"][0]
    # y is the terrain height Expansion wrote — a top-down drag has no opinion.
    assert zone["Center"] == [100.0, 403.0, 200.0]
    assert zone["Radius"] == 500.0
    # Everything else in the file survives.
    assert _json(path)["ItemLifetimeInSafeZone"] == 900.0


def test_resizing_a_circle_and_a_cylinder(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "SafeZoneSettings.json"
    client.put("/api/map-editor/expansion/safezones/circle/0", json={"radius": 250})
    client.put("/api/map-editor/expansion/safezones/cylinder/0", json={"height": 80})
    data = _json(path)
    assert data["CircleZones"][0]["Radius"] == 250.0
    assert data["CylinderZones"][0]["Height"] == 80.0


def test_moving_one_polygon_vertex_leaves_the_others(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "SafeZoneSettings.json"
    resp = client.put(
        "/api/map-editor/expansion/safezones/polygon/0",
        json={"point": 1, "game_x": 500, "game_z": 600},
    )
    assert resp.status_code == 200
    positions = _json(path)["PolygonZones"][0]["Positions"]
    assert positions[1] == [500.0, 139.8, 600.0]
    assert positions[0] == SAFEZONES["PolygonZones"][0]["Positions"][0]


def test_a_polygon_needs_a_vertex_and_a_bad_index_is_a_404(client):
    assert (
        client.put("/api/map-editor/expansion/safezones/polygon/0", json={"game_x": 1}).status_code
        == 404
    )
    assert (
        client.put(
            "/api/map-editor/expansion/safezones/polygon/0", json={"point": 9, "game_x": 1}
        ).status_code
        == 404
    )
    assert (
        client.put("/api/map-editor/expansion/safezones/circle/7", json={"radius": 1}).status_code
        == 404
    )
    # A kind that isn't one of the three is a 404, not a 500.
    assert (
        client.put("/api/map-editor/expansion/safezones/blob/0", json={"radius": 1}).status_code
        == 404
    )


# ── trader zones ──────────────────────────────────────────────────


def test_trader_zones_never_carry_the_stock_table(client):
    (zone,) = client.get("/api/map-editor/expansion/trader-zones").json()["trader_zones"]
    assert zone["id"] == "BalotaAircrafts"
    assert zone["name"] == "Balota Aircrafts Trading Zone"
    assert zone["radius"] == 100.0
    assert (zone["buy_percent"], zone["sell_percent"]) == (100.0, -1.0)
    # The count is the whole of what the map needs from a 63 kB stock table.
    assert zone["stock_items"] == 2
    assert "Stock" not in zone and "stock" not in zone


def test_editing_a_trader_zone_preserves_its_stock(client, expansion_mission):
    path = expansion_mission / "expansion" / "traderzones" / "BalotaAircrafts.json"
    resp = client.put(
        "/api/map-editor/expansion/trader-zones/BalotaAircrafts",
        json={"game_x": 1, "game_z": 2, "radius": 250, "buy_percent": 110, "sell_percent": 80},
    )
    assert resp.status_code == 200

    data = _json(path)
    assert data["Position"] == [1.0, 6.0, 2.0]
    assert data["Radius"] == 250.0
    assert data["BuyPricePercent"] == 110.0
    assert data["SellPricePercent"] == 80.0
    assert data["Stock"] == TRADER_ZONE["Stock"]
    assert data["m_Version"] == 6


def test_an_unknown_trader_zone_is_a_404_and_writes_nothing(client, expansion_mission):
    resp = client.put("/api/map-editor/expansion/trader-zones/Nope", json={"radius": 1})
    assert resp.status_code == 404
    assert not (expansion_mission / "expansion" / "traderzones" / "Nope.json").exists()


@pytest.mark.asyncio
async def test_a_zone_id_is_never_joined_onto_a_path(expansion_mission, sample_config):
    """The id addresses a loaded zone, so a traversal names nothing."""
    from app.spawn_editor.expansion_state import ExpansionTraderZoneState

    class _Silent:
        async def broadcast(self, message):
            pass

    state = ExpansionTraderZoneState(expansion_mission, "chernarusplus", _Silent())
    state.load()
    assert await state.update_zone("../settings/SpawnSettings", {"radius": 1}) is None
    assert await state.update_zone("../../serverDZ", {"radius": 1}) is None
    # The one real zone still reads back unchanged.
    (zone,) = state.get_json()["trader_zones"]
    assert zone["radius"] == 100.0


# ── spawn points ──────────────────────────────────────────────────


def test_spawn_points_are_grouped_by_location(client):
    body = client.get("/api/map-editor/expansion/spawn-points").json()
    (location,) = body["spawn_locations"]
    assert location["name"] == "Svetloyarsk"
    assert location["use_cooldown"] is True
    assert [p["index"] for p in location["positions"]] == [0, 1]
    assert location["positions"][0]["game_x"] == 14273.2
    assert body["enabled"] is True


def test_spawn_selection_is_reported_from_the_file(client, expansion_mission):
    """`enabled` is Expansion's EnableSpawnSelection, and the Players tab reads
    it to say which of the two spawn systems is live: off means players spawn
    from the mission's cfgPlayerSpawnPoints.xml instead."""
    path = expansion_mission / "expansion" / "settings" / "SpawnSettings.json"
    data = _json(path)
    data["EnableSpawnSelection"] = 0
    _write(path, data)

    # Written before the first map-editor request, so this is what the state
    # loads — the editor reads the file once per session and then watches it.
    body = client.get("/api/map-editor/expansion/spawn-points").json()
    assert body["enabled"] is False
    # The locations are still listed — configured but unused is worth seeing.
    assert len(body["spawn_locations"]) == 1


def test_moving_a_spawn_point_keeps_the_rest_of_the_file(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "SpawnSettings.json"
    resp = client.put(
        "/api/map-editor/expansion/spawn-points/0/1", json={"game_x": 10, "game_z": 20}
    )
    assert resp.status_code == 200

    data = _json(path)
    # Height 0: Expansion puts it on the ground wherever it now is.
    assert data["SpawnLocations"][0]["Positions"][1] == [10.0, 0.0, 20.0]
    assert data["SpawnLocations"][0]["Positions"][0] == SPAWNS["SpawnLocations"][0]["Positions"][0]
    # The loadout/gear half of the file is untouched.
    assert data["StartingGear"] == SPAWNS["StartingGear"]


def test_an_out_of_range_spawn_point_is_a_404(client):
    assert (
        client.put("/api/map-editor/expansion/spawn-points/0/9", json={"game_x": 1}).status_code
        == 404
    )
    assert (
        client.put("/api/map-editor/expansion/spawn-points/5/0", json={"game_x": 1}).status_code
        == 404
    )


# ── missions ──────────────────────────────────────────────────────


def test_missions_read_both_shapes_and_skip_what_they_cannot_place(client):
    missions = client.get("/api/map-editor/expansion/missions").json()["missions"]
    by_kind = {m["kind"]: m for m in missions}
    assert set(by_kind) == {"airdrop", "contaminated"}

    assert by_kind["airdrop"]["name"] == "Balota"
    assert (by_kind["airdrop"]["game_x"], by_kind["airdrop"]["game_z"]) == (5043.0, 2505.0)
    assert by_kind["airdrop"]["radius"] == 100.0

    assert by_kind["contaminated"]["game_z"] == 14968.0
    assert by_kind["contaminated"]["radius"] == 200.0
    # A mission file with neither shape is skipped, not placed at 0,0.
    assert all(m["id"] != "Something_New" for m in missions)


def test_missions_are_read_only(client):
    # There is no write route in this cut, and an absent one must 404/405
    # rather than silently do nothing.
    resp = client.put("/api/map-editor/expansion/missions/Airdrop_Random_Balota", json={})
    assert resp.status_code in (404, 405)


# ── a server Expansion has never run on ───────────────────────────


def test_every_layer_answers_empty_before_the_first_boot(bare_client):
    base = "/api/map-editor/expansion"
    assert bare_client.get(f"{base}/safezones").json() == {
        "circles": [],
        "cylinders": [],
        "polygons": [],
        "enabled": False,
    }
    assert bare_client.get(f"{base}/trader-zones").json() == {"trader_zones": []}
    assert bare_client.get(f"{base}/spawn-points").json() == {
        "spawn_locations": [],
        "enabled": False,
    }
    assert bare_client.get(f"{base}/missions").json() == {"missions": []}


def test_a_write_before_the_first_boot_says_what_is_missing(bare_client):
    resp = bare_client.put("/api/map-editor/expansion/safezones/circle/0", json={"radius": 1})
    assert resp.status_code == 404
    assert "SafeZoneSettings.json" in resp.json()["detail"]

    resp = bare_client.put("/api/map-editor/expansion/trader-zones/Kamenka", json={})
    assert resp.status_code == 404
    assert "trader zones" in resp.json()["detail"]


def test_the_layers_need_a_session(sample_config, expansion_mission):
    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/map-editor/expansion/safezones").status_code == 401


# ── the file watcher's routing ────────────────────────────────────


def test_the_watcher_routes_expansion_changes_by_file_and_by_folder():
    """Two of the four layers are folders whose filenames the server picks,
    so those route on the parent directory."""
    from app.spawn_editor.watcher import expansion_reloads

    states = {
        "safezone": "SZ",
        "spawn": "SP",
        "traderzone": "TZ",
        "mission": "MI",
        "ai_patrol": "AP",
        "ai_location": "AL",
    }
    mission = Path("/srv/mpmissions/dayzOffline.chernarusplus")

    assert expansion_reloads({mission / "expansion/settings/SafeZoneSettings.json"}, states) == [
        "SZ"
    ]
    assert expansion_reloads({mission / "expansion/settings/SpawnSettings.json"}, states) == ["SP"]
    # Any file in the folder, whatever it is called.
    assert expansion_reloads({mission / "expansion/traderzones/Kamenka.json"}, states) == ["TZ"]
    assert expansion_reloads({mission / "expansion/missions/Airdrop_Random_X.json"}, states) == [
        "MI"
    ]
    # The AI module's two files route by name, like the other settings files.
    assert expansion_reloads({mission / "expansion/settings/AIPatrolSettings.json"}, states) == [
        "AP"
    ]
    assert expansion_reloads({mission / "expansion/settings/AILocationSettings.json"}, states) == [
        "AL"
    ]
    # Unrelated writes, and layers that aren't loaded, route nowhere.
    assert expansion_reloads({mission / "db/types.xml"}, states) == []
    assert expansion_reloads({mission / "expansion/settings/MapSettings.json"}, states) == []
    assert expansion_reloads({mission / "expansion/traderzones/Kamenka.json"}, {}) == []


# ── AI patrols and roaming locations ──────────────────────────────


def test_patrols_draw_their_route_and_count_what_cannot_be_placed(client):
    body = client.get("/api/map-editor/expansion/ai/patrols").json()
    assert [p["name"] for p in body["patrols"]] == ["NWAF Guards"]
    assert len(body["patrols"][0]["waypoints"]) == 3
    assert body["patrols"][0]["number_of_ai"] == 4
    # The object patrol has no waypoints — reported, never silently dropped.
    assert body["unplaceable_patrols"] == 1


def test_moving_a_waypoint_keeps_its_height_and_the_patrol(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "AIPatrolSettings.json"
    assert (
        client.put(
            "/api/map-editor/expansion/ai/patrols/0/waypoints/1",
            json={"game_x": 5000.0, "game_z": 10500.0},
        ).status_code
        == 200
    )
    patrol = _json(path)["Patrols"][0]
    assert patrol["Waypoints"][1] == [5000.0, 291.0, 10500.0]
    assert patrol["Loadout"] == "MercenaryLoadout"
    assert len(patrol["Waypoints"]) == 3


def test_a_new_waypoint_is_appended_at_the_last_ones_height(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "AIPatrolSettings.json"
    body = client.post(
        "/api/map-editor/expansion/ai/patrols/0/waypoints",
        json={"lat": 100.0, "lng": 100.0},
    )
    assert body.status_code == 200
    points = _json(path)["Patrols"][0]["Waypoints"]
    assert len(points) == 4
    # 0 would be under the terrain nearly everywhere on the map.
    assert points[3][1] == 292.0


def test_the_last_waypoint_cannot_be_deleted(client, expansion_mission):
    """Expansion skips a patrol with no waypoints (plans/64 B6)."""
    base = "/api/map-editor/expansion/ai/patrols/0/waypoints"
    assert client.delete(f"{base}/2").status_code == 200
    assert client.delete(f"{base}/1").status_code == 200
    refused = client.delete(f"{base}/0")
    assert refused.status_code == 409
    assert "at least one waypoint" in refused.json()["detail"]
    path = expansion_mission / "expansion" / "settings" / "AIPatrolSettings.json"
    assert len(_json(path)["Patrols"][0]["Waypoints"]) == 1


def test_an_object_patrol_with_waypoints_is_counted_not_drawn_or_edited(client, expansion_mission):
    """Expansion's own default object patrols carry one waypoint a few metres
    from the building (plans/64 B4, B5): it is not a map position."""
    path = expansion_mission / "expansion" / "settings" / "AIPatrolSettings.json"
    tower = {"Name": "", "ObjectClassName": "Land_Mil_GuardTower", "NumberOfAI": 1,
             "Waypoints": [[0.5, 0.0, 2.0]]}
    _write(path, {**AI_PATROLS, "Patrols": [*AI_PATROLS["Patrols"], tower]})
    assert client.post("/api/map-editor/reload").status_code == 200
    body = client.get("/api/map-editor/expansion/ai/patrols").json()
    assert [p["name"] for p in body["patrols"]] == ["NWAF Guards"]
    assert (body["unplaceable_patrols"], body["object_patrols"]) == (2, 2)
    base = "/api/map-editor/expansion/ai/patrols/2/waypoints"
    for resp in (
        client.put(f"{base}/0", json={"game_x": 5000.0, "game_z": 10500.0}),
        client.post(base, json={"lat": 100.0, "lng": 100.0}),
        client.delete(f"{base}/0"),
    ):
        assert resp.status_code == 409 and "Raw tab" in resp.json()["detail"]
    assert _json(path)["Patrols"][2] == tower


def test_deleting_a_patrol_leaves_the_others(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "AIPatrolSettings.json"
    assert client.delete("/api/map-editor/expansion/ai/patrols/0").status_code == 200
    patrols = _json(path)["Patrols"]
    assert [p["Name"] for p in patrols] == ["Heli crash guards"]


def test_roaming_locations_and_no_go_areas_are_separate(client):
    body = client.get("/api/map-editor/expansion/ai/locations").json()
    assert [loc["name"] for loc in body["roaming_locations"]] == ["Kamenka", "Balota"]
    assert body["roaming_locations"][1]["enabled"] is False
    assert body["roaming_locations"][1]["type"] == "Military"
    # One no-go area is placeable, the other has no position at all.
    assert [area["name"] for area in body["no_go_areas"]] == ["Trader"]
    assert body["skipped_no_go_areas"] == 1


def test_editing_a_roaming_location_keeps_the_files_flag_form(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "AILocationSettings.json"
    assert (
        client.put(
            "/api/map-editor/expansion/ai/locations/1",
            json={"radius": 450, "enabled": True, "name": "Balota airfield"},
        ).status_code
        == 200
    )
    entry = _json(path)["RoamingLocations"][1]
    assert entry["Radius"] == 450
    assert entry["Name"] == "Balota airfield"
    # Expansion writes 0/1, not true/false.
    assert entry["Enabled"] == 1
    assert entry["Type"] == "Military"
    # Nothing else in the file moved.
    assert _json(path)["ExcludedRoamingBuildings"] == ["Land_Factory_Small"]


def test_a_new_roaming_location_is_copied_from_an_existing_one(client, expansion_mission):
    """Type is one of Expansion's own location kinds — the editor never invents
    one, so a new location is always a copy of one that already works."""
    path = expansion_mission / "expansion" / "settings" / "AILocationSettings.json"
    body = client.post(
        "/api/map-editor/expansion/ai/locations/1/duplicate",
        json={"lat": -40.0, "lng": -50.0, "name": "Balota south"},
    )
    assert body.status_code == 200
    created = _json(path)["RoamingLocations"][-1]
    assert created["Name"] == "Balota south"
    assert created["Type"] == "Military"
    assert created["Radius"] == 300.0
    assert created["Position"][0] != 4500.0


def test_deleting_a_roaming_location(client, expansion_mission):
    path = expansion_mission / "expansion" / "settings" / "AILocationSettings.json"
    assert client.delete("/api/map-editor/expansion/ai/locations/0").status_code == 200
    assert [loc["Name"] for loc in _json(path)["RoamingLocations"]] == ["Balota"]


def test_the_ai_layers_answer_empty_before_the_module_has_run(bare_client):
    patrols = bare_client.get("/api/map-editor/expansion/ai/patrols")
    locations = bare_client.get("/api/map-editor/expansion/ai/locations")
    assert patrols.json() == {"patrols": [], "unplaceable_patrols": 0, "object_patrols": 0}
    assert locations.json()["roaming_locations"] == []
    # A write says which file is missing, rather than answering 200.
    missing = bare_client.delete("/api/map-editor/expansion/ai/patrols/0")
    assert missing.status_code == 404
    assert "AIPatrolSettings.json" in missing.json()["detail"]


# ── TraderPlus beside Expansion Market ────────────────────────────


TP_GENERAL = {
    "Traders": [
        # Inside the Balota zone (4971, 2437, radius 100).
        {"Id": 1, "Name": "Boris", "GivenName": "Aircraft Dealer", "Position": [5000.0, 6.0, 2450.0]},
        # A kilometre away from it.
        {"Id": 2, "Name": "Ivan", "GivenName": "Gun Dealer", "Position": [6500.0, 6.0, 2437.0]},
    ],
    "TraderObjects": [],
}

TP_SAFEZONES = {
    "SafeAreaLocation": [
        # Its own 150 m circle 200 m away: the two circles overlap.
        {"SafeZoneStatut": "Balota", "X": 5171.0, "Y": 2437.0, "Radius": 150},
    ]
}


@pytest.fixture()
def traderplus_config(sample_config) -> Path:
    """TraderPlus' two map-data files, where the mod itself writes them."""
    cfg_dir = (
        Path(sample_config.server_dir_path)
        / sample_config.profiles_path
        / "TraderPlus"
        / "TraderPlusConfig"
    )
    _write(cfg_dir / "TraderPlusGeneralConfig.json", TP_GENERAL)
    _write(cfg_dir / "TraderPlusSafeZoneConfig.json", TP_SAFEZONES)
    return cfg_dir


def test_one_trader_system_reports_no_overlap(client):
    """Expansion alone: the panel that asks must be able to stay quiet."""
    body = client.get("/api/map-editor/trader-systems").json()
    assert body["expansion"] is True
    assert body["traderplus"] is False
    assert body["both"] is False
    assert body["overlaps"] == []


def test_both_trader_systems_report_where_they_meet(traderplus_config, client):
    body = client.get("/api/map-editor/trader-systems").json()
    assert body["both"] is True
    assert body["traderplus_traders"] == 2
    assert body["expansion_zones"] == 1
    kinds = {(o["kind"], o["traderplus"]) for o in body["overlaps"]}
    # The dealer standing in the zone and the safe zone whose circle crosses
    # it — never the trader a kilometre away.
    assert kinds == {("trader", "Aircraft Dealer"), ("safezone", "Balota")}
    assert body["overlap_count"] == 2


def test_overlaps_are_measured_on_the_ground_plane():
    """A trader on a rooftop is still inside the zone below it."""
    from app.spawn_editor.trader_systems import find_overlaps

    zone = {"name": "Zone", "game_x": 0.0, "game_z": 0.0, "radius": 100.0}
    on_the_roof = {"index": 0, "name": "Up", "game_x": 10.0, "game_y": 900.0, "game_z": 10.0}
    just_outside = {"index": 1, "name": "Out", "game_x": 101.0, "game_y": 0.0, "game_z": 0.0}

    found = find_overlaps([on_the_roof, just_outside], [], [zone])
    assert [o["traderplus"] for o in found] == ["Up"]


def test_the_overlap_list_is_capped_but_the_count_is_not():
    """A sidebar panel is not a place for a thousand rows."""
    from app.spawn_editor.trader_systems import MAX_REPORTED, analyze

    zone = {"name": "Zone", "game_x": 0.0, "game_z": 0.0, "radius": 1000.0}
    traders = [
        {"index": i, "name": f"T{i}", "game_x": float(i), "game_z": 0.0}
        for i in range(MAX_REPORTED + 10)
    ]
    body = analyze(traders, [], [zone])
    assert body["overlap_count"] == MAX_REPORTED + 10
    assert len(body["overlaps"]) == MAX_REPORTED
    # Closest first, so what is shown is the tightest overlap.
    assert body["overlaps"][0]["distance"] == 0


def test_a_moved_spawn_point_is_put_on_the_ground_by_expansion(client, expansion_mission):
    """Expansion's respawn handler replaces a height of 0 with the ground height
    and uses any other height as-is. A coastal point dragged inland used to keep
    its 4.3 m and spawned players about 335 m under NWAF (2026 Q3)."""
    path = expansion_mission / "expansion" / "settings" / "SpawnSettings.json"
    original = SPAWNS["SpawnLocations"][0]["Positions"][1]
    # A save that does not move it keeps the height the file had.
    client.put(
        "/api/map-editor/expansion/spawn-points/0/1",
        json={"game_x": original[0], "game_z": original[2]},
    )
    assert _json(path)["SpawnLocations"][0]["Positions"][1][1] == original[1]
    client.put("/api/map-editor/expansion/spawn-points/0/1", json={"game_x": 4548.82, "game_z": 10107.72})
    assert _json(path)["SpawnLocations"][0]["Positions"][1] == [4548.82, 0.0, 10107.72]
