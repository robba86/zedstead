"""API tests for the map editor's routes (app/spawn_editor/routes.py).

Driven through the web app, which is the only thing that serves them since the
standalone editor was deleted — so these also exercise the session guard.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PASSWORD = "testpass123"


def _client(config) -> TestClient:
    """Logged-in client for a web app whose editor routes are reachable."""
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(config))
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture()
def client(sample_config, tmp_mission_dir):
    # tmp_mission_dir ensures mpmissions/dayzOffline.chernarusplus/db/ exists so
    # the state loaders find the mission and load empty (no crash).
    return _client(sample_config)


@pytest.fixture()
def seeded_client(sample_config, tmp_mission_dir):
    """Client with minimal events.xml + cfgeventspawns.xml seeded for mutation tests."""
    db_dir = tmp_mission_dir / "db"
    (db_dir / "events.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <events>
            </events>
        """),
        encoding="utf-8",
    )
    (tmp_mission_dir / "cfgeventspawns.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <eventposdef>
            </eventposdef>
        """),
        encoding="utf-8",
    )
    return _client(sample_config)


def test_editor_routes_require_a_session(sample_config, tmp_mission_dir):
    from app.web import server

    anon = TestClient(server.create_app(sample_config))
    assert anon.get("/api/map-editor/maps").status_code == 401


# ---------------------------------------------------------------------------
# Step 3: Read-endpoint tests
# ---------------------------------------------------------------------------

def test_get_maps_lists_terrains_with_current(client):
    resp = client.get("/api/map-editor/maps")
    assert resp.status_code == 200
    maps = resp.json()
    assert isinstance(maps, list) and maps
    terrains = {m["terrain"] for m in maps}
    assert "chernarusplus" in terrains
    assert any(m["current"] for m in maps)


def test_get_maps_lists_the_whole_map_catalog(client):
    """The picker is the app's map list, not the calibration table's keys."""
    from app.maps.registry import known_terrains

    maps = client.get("/api/map-editor/maps").json()
    assert {m["terrain"] for m in maps} >= set(known_terrains())
    assert {"terrain": "enoch", "name": "Livonia", "current": False} in maps


def test_get_map_info_shape(client):
    resp = client.get("/api/map-editor/map-info")
    assert resp.status_code == 200
    info = resp.json()
    for key in ("terrain", "map_size", "bounds", "scale", "traderplus"):
        assert key in info
    assert info["traderplus"] is False


def test_get_spawns_shape(client):
    resp = client.get("/api/map-editor/spawns")
    assert resp.status_code == 200
    data = resp.json()
    assert "terrain" in data
    assert isinstance(data["events"], list)


def test_get_help_serves_the_guide(client):
    """The guide JSON lives in app/data/ and is only reachable over this
    route, which the editor's ? dialog renders."""
    resp = client.get("/api/map-editor/help")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"]
    assert data["sections"]


def test_get_events_returns_list(client):
    resp = client.get("/api/map-editor/events")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_event_detail_unknown_is_404(client):
    resp = client.get("/api/map-editor/events/DoesNotExist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Step 4: Mutation tests
# ---------------------------------------------------------------------------

def test_create_event_requires_name(seeded_client):
    resp = seeded_client.post("/api/map-editor/events", json={"name": "  "})
    assert resp.status_code == 400


def test_create_event_round_trip(seeded_client):
    resp = seeded_client.post("/api/map-editor/events", json={"name": "TestEvent"})
    assert resp.status_code == 200
    listed = seeded_client.get("/api/map-editor/events").json()
    assert any(e.get("name") == "TestEvent" for e in listed)


def test_create_event_conflicts_when_both_files_have_it(seeded_client):
    assert seeded_client.post("/api/map-editor/events", json={"name": "Dup"}).status_code == 200
    resp = seeded_client.post("/api/map-editor/events", json={"name": "Dup"})
    assert resp.status_code == 409


def test_create_event_fills_in_a_missing_eventspawns_entry(sample_config, tmp_mission_dir):
    """An events.xml-only event can be given a cfgeventspawns.xml entry.

    Mods ship events with no positions of their own; without this the map
    editor's "View <event> on map" link was a dead end — the event is invisible
    there (no positions) AND the create dialog answered 409.
    """
    import textwrap

    db_dir = tmp_mission_dir / "db"
    (db_dir / "events.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <events>
                <event name="VehicleOnlyInEvents">
                    <nominal>1</nominal>
                    <min>1</min>
                    <max>2</max>
                    <lifetime>300</lifetime>
                    <restock>0</restock>
                    <saferadius>100</saferadius>
                    <distanceradius>200</distanceradius>
                    <cleanupradius>300</cleanupradius>
                    <flags deletable="0" init_random="0" remove_damaged="1"/>
                    <position>fixed</position>
                    <limit>custom</limit>
                    <active>1</active>
                </event>
            </events>
        """),
        encoding="utf-8",
    )
    (tmp_mission_dir / "cfgeventspawns.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <eventposdef>
            </eventposdef>
        """),
        encoding="utf-8",
    )
    client = _client(sample_config)

    # Not on the map yet: /spawns only reports cfgeventspawns.xml entries.
    spawns = client.get("/api/map-editor/spawns").json()
    assert not any(e["name"] == "VehicleOnlyInEvents" for e in spawns["events"])

    # ... and it is offered as a suggestion rather than filtered out as existing.
    names = client.get("/api/map-editor/type-names").json()
    suggested = [n["name"] for s in names["sources"] for n in s["names"]]
    assert "VehicleOnlyInEvents" in suggested

    resp = client.post("/api/map-editor/events", json={"name": "VehicleOnlyInEvents"})
    assert resp.status_code == 200

    spawns = client.get("/api/map-editor/spawns").json()
    assert any(e["name"] == "VehicleOnlyInEvents" for e in spawns["events"])
    # events.xml untouched — still exactly one entry, with its own values.
    listed = client.get("/api/map-editor/events").json()
    matches = [e for e in listed if e["name"] == "VehicleOnlyInEvents"]
    assert len(matches) == 1
    assert matches[0]["nominal"] == 1


def _ce_defined_client(sample_config, tmp_mission_dir):
    """Client whose mission defines an event only through a mod's custom/ CE file."""
    import textwrap

    (tmp_mission_dir / "db" / "events.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<events>\n</events>\n',
        encoding="utf-8",
    )
    (tmp_mission_dir / "cfgeventspawns.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<eventposdef>\n</eventposdef>\n',
        encoding="utf-8",
    )
    (tmp_mission_dir / "cfgeconomycore.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <economycore>
                <ce folder="custom">
                    <file name="dayz-bicycle-events.xml" type="events" />
                </ce>
            </economycore>
        """),
        encoding="utf-8",
    )
    custom = tmp_mission_dir / "custom"
    custom.mkdir(exist_ok=True)
    (custom / "dayz-bicycle-events.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <events>
                <event name="VehicleHunterz_Old_Bike">
                    <nominal>10</nominal>
                    <min>5</min>
                    <max>15</max>
                    <lifetime>300</lifetime>
                    <restock>0</restock>
                    <saferadius>100</saferadius>
                    <distanceradius>200</distanceradius>
                    <cleanupradius>300</cleanupradius>
                    <flags deletable="0" init_random="0" remove_damaged="1"/>
                    <position>fixed</position>
                    <limit>custom</limit>
                    <active>1</active>
                </event>
            </events>
        """),
        encoding="utf-8",
    )
    return _client(sample_config)


def test_ce_defined_event_gets_only_its_spawn_entry(sample_config, tmp_mission_dir):
    """A mod's CE-linked event must not be duplicated into db/events.xml.

    Its definition is already live (cfgeconomycore loads custom/), so the only
    half the map editor can add is the cfgeventspawns.xml position list.
    """
    client = _ce_defined_client(sample_config, tmp_mission_dir)
    name = "VehicleHunterz_Old_Bike"

    resp = client.post("/api/map-editor/events", json={"name": name})
    assert resp.status_code == 200
    assert resp.json()["defined_in"] == "custom"

    spawns = client.get("/api/map-editor/spawns").json()
    assert any(e["name"] == name for e in spawns["events"])
    # db/events.xml is untouched — the definition stays the mod's.
    assert name not in (tmp_mission_dir / "db" / "events.xml").read_text(encoding="utf-8")
    assert not any(e["name"] == name for e in client.get("/api/map-editor/events").json())

    # A second create is a conflict now that both halves exist.
    assert client.post("/api/map-editor/events", json={"name": name}).status_code == 409


def test_ce_defined_event_is_suggested_and_explained(sample_config, tmp_mission_dir):
    client = _ce_defined_client(sample_config, tmp_mission_dir)
    name = "VehicleHunterz_Old_Bike"

    names = client.get("/api/map-editor/type-names").json()
    suggested = {n["name"] for s in names["sources"] for n in s["names"]}
    assert name in suggested

    resp = client.get(f"/api/map-editor/events/{name}")
    assert resp.status_code == 404
    assert "custom/" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# plans/51 B: write bodies are checked before anything is written
# ---------------------------------------------------------------------------

def _seed_spawn(client, name="PlanFiftyOne", angle=45.0) -> Path:
    assert client.post("/api/map-editor/events", json={"name": name}).status_code == 200
    resp = client.post(f"/api/map-editor/spawns/{name}", json={"lat": -100.0, "lng": 100.0, "angle": angle})
    assert resp.status_code == 200, resp.text
    return name


def _spawns_file(sample_config) -> bytes:
    return next(sample_config.server_dir_path.glob("mpmissions/*/cfgeventspawns.xml")).read_bytes()


@pytest.mark.parametrize("body", [
    {},                                  # N21: KeyError 'lat' -> bare 500
    {"lat": "abc", "lng": 1.0},          # N21: TypeError -> bare 500
    {"lat": None, "lng": 1.0},           # an emptied popup field arrives as null
    {"lat": True, "lng": 1.0},
    {"lat": 1e12, "lng": 1.0},
])
def test_a_bad_spawn_move_is_a_400_and_writes_nothing(seeded_client, sample_config, body):
    name = _seed_spawn(seeded_client)
    before = _spawns_file(sample_config)
    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0", json=body)
    assert resp.status_code == 400, resp.text
    assert _spawns_file(sample_config) == before


def test_nan_never_reaches_the_spawn_file(seeded_client, sample_config):
    """N20: `{"lat":NaN}` wrote `x="nan"` into cfgeventspawns.xml, then 500'd."""
    name = _seed_spawn(seeded_client)
    before = _spawns_file(sample_config)
    resp = seeded_client.put(
        f"/api/map-editor/spawns/{name}/0",
        content=b'{"lat": NaN, "lng": NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "NaN" in resp.json()["detail"]
    assert _spawns_file(sample_config) == before


def test_a_spawn_move_without_an_angle_keeps_the_angle(seeded_client):
    """The route used to default a missing angle to -1 ("random")."""
    name = _seed_spawn(seeded_client, angle=45.0)
    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0", json={"lat": -110.0, "lng": 100.0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["angle"] == 45.0

    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0/game", json={"x": 500.0, "z": 600.0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["angle"] == 45.0


def test_an_explicit_random_angle_is_still_written(seeded_client):
    name = _seed_spawn(seeded_client, angle=45.0)
    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0", json={"lat": -110.0, "lng": 100.0, "angle": -1})
    assert resp.status_code == 200
    assert resp.json()["angle"] == -1


def test_a_body_less_write_still_works(seeded_client):
    """The check skips an empty body: `PUT /events/{name}/active` sends `{}`
    and other writes send nothing at all."""
    name = _seed_spawn(seeded_client)
    assert seeded_client.put(f"/api/map-editor/events/{name}/active").status_code == 200
    assert seeded_client.put(f"/api/map-editor/events/{name}/active", json={}).status_code == 200


# ---------------------------------------------------------------------------
# Positions are bounded by the map; untouched ones stay put (test pass 1, F9/F10)
# ---------------------------------------------------------------------------


def test_an_off_map_game_position_is_refused(seeded_client, sample_config):
    """F9: X = 99999 was written, and the marker left the map image, so it
    could not be clicked to fix it."""
    name = _seed_spawn(seeded_client)
    before = _spawns_file(sample_config)

    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0/game", json={"x": 99999, "z": 600.0})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "x is outside the map (0–15360)"
    assert _spawns_file(sample_config) == before


def test_an_off_map_marker_drag_is_refused(seeded_client, sample_config):
    """The lat/lng a drag sends is converted with the mission's terrain."""
    name = _seed_spawn(seeded_client)
    before = _spawns_file(sample_config)

    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0", json={"lat": -120.0, "lng": 110.0})

    assert resp.status_code == 400
    assert "outside the map" in resp.json()["detail"]
    assert _spawns_file(sample_config) == before


def test_height_is_not_bounded():
    from app.spawn_editor.routes import _off_map_problem

    assert _off_map_problem({"x": 100.0, "y": 99999.0, "z": 100.0}, "chernarusplus") is None


def test_a_map_with_no_known_size_gets_no_bound():
    """Guessing Chernarus' size would refuse valid positions on a larger map."""
    from app.spawn_editor.routes import _off_map_problem

    assert _off_map_problem({"x": 99999.0, "z": 5.0}, "somecommunitymap") is None
    assert _off_map_problem({"x": 99999.0, "z": 5.0}, None) is None
    assert _off_map_problem({"x": 99999.0, "z": 5.0}, "enoch") == "x is outside the map (0–12800)"


def _precise_spawn(client, sample_config, name):
    """Give the seeded spawn more decimals than the payload shows, as
    Bohemia's files have, and return the file and the position as shown."""
    path = next(sample_config.server_dir_path.glob("mpmissions/*/cfgeventspawns.xml"))
    path.write_text(re.sub(r'<pos [^>]*/>', '<pos x="1500.123456" z="850.336915" a="45.000000" />',
                           path.read_text()))
    assert client.post("/api/map-editor/reload").status_code == 200
    [pos] = next(e for e in client.get("/api/map-editor/spawns").json()["events"]
                 if e["name"] == name)["positions"]
    assert (pos["game_x"], pos["game_z"]) == (1500.12, 850.34)
    return path, pos


def test_a_save_that_changes_nothing_writes_nothing(seeded_client, sample_config):
    """F10: the popup wrote back the rounded value it displayed, so a no-op
    Save moved an untouched coordinate and was a diff."""
    name = _seed_spawn(seeded_client, angle=45.0)
    path, pos = _precise_spawn(seeded_client, sample_config, name)
    before = path.read_bytes()

    resp = seeded_client.put(
        f"/api/map-editor/spawns/{name}/0/game",
        json={"x": pos["game_x"], "z": pos["game_z"], "angle": 45.0},
    )

    assert resp.status_code == 200, resp.text
    assert path.read_bytes() == before


def test_an_edited_x_keeps_the_untouched_z_exact(seeded_client, sample_config):
    name = _seed_spawn(seeded_client, angle=45.0)
    path, pos = _precise_spawn(seeded_client, sample_config, name)

    resp = seeded_client.put(
        f"/api/map-editor/spawns/{name}/0/game",
        json={"x": 1234.5, "z": pos["game_z"], "angle": 45.0},
    )

    assert resp.status_code == 200, resp.text
    text = path.read_text()
    assert 'x="1234.500000"' in text and 'z="850.336915"' in text


def _stock_shaped_spawn(client, sample_config, name, pos_line):
    """Replace the seeded position with one written as Bohemia writes them."""
    path = next(sample_config.server_dir_path.glob("mpmissions/*/cfgeventspawns.xml"))
    path.write_text(re.sub(r'<pos [^>]*/>', pos_line, path.read_text()))
    assert client.post("/api/map-editor/reload").status_code == 200
    [pos] = next(e for e in client.get("/api/map-editor/spawns").json()["events"]
                 if e["name"] == name)["positions"]
    return path, pos


@pytest.mark.parametrize("route", ["game", "latlng"])
def test_a_move_keeps_the_height_and_says_so(seeded_client, sample_config, route):
    """StaticSantaCrash and the trains place their object at the file's `y`.
    The map has no heights, so a move keeps it and the answer says so."""
    name = _seed_spawn(seeded_client)
    # Verbatim from Bohemia's 1.29 Chernarus cfgeventspawns.xml.
    path, pos = _stock_shaped_spawn(
        seeded_client, sample_config, name, '<pos x="2593.47" z="5094.11" a="0" y="201.66"/>')
    assert pos["height"] == 201.66 and pos["angle"] == 0.0

    if route == "game":
        resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0/game",
                                 json={"x": 2700.0, "z": pos["game_z"], "angle": pos["angle"]})
    else:
        resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0",
                                 json={"lat": pos["lat"] + 1, "lng": pos["lng"], "angle": pos["angle"]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["height_kept"] is True
    text = path.read_text()
    assert 'a="0" y="201.66"' in text


def test_a_position_without_a_height_says_nothing_about_one(seeded_client, sample_config):
    """A position with no `a` gains none when the request sends the shown 0."""
    name = _seed_spawn(seeded_client)
    # Verbatim from Bohemia's 1.29 Chernarus cfgeventspawns.xml.
    path, pos = _stock_shaped_spawn(seeded_client, sample_config, name, '<pos x="8098.73" z="13023.365234" />')
    resp = seeded_client.put(f"/api/map-editor/spawns/{name}/0/game",
                             json={"x": 8000.0, "z": pos["game_z"], "angle": pos["angle"]})
    assert resp.status_code == 200
    assert "height_kept" not in resp.json() and resp.json()["height"] is None
    assert " a=" not in path.read_text()


# A garage NPC as TraderPlus writes it: 17 significant digits, never an
# exponent, orientations to five decimals.
GARAGE_TEXT = """{
    "NPCs": [
        {
            "ClassName": "SurvivorM_Mirek",
            "Position": [
                1647.6500244140626,
                211.8800048828125,
                13000.330078125
            ],
            "Orientation": [
                222.97168,
                0.0,
                0.0
            ],
            "ParkingPosition": [
                1660.4000244140626,
                212.0,
                -0.000003000000106112566
            ],
            "ParkingOrientation": [
                90.00001,
                0.0,
                0.0
            ],
            "Clothes": []
        }
    ]
}
"""


def _garage_client(sample_config) -> tuple[TestClient, Path]:
    from app.traderplus.trader_common import _profiles_dir

    folder = _profiles_dir(sample_config) / "TraderPlus" / "TraderPlusConfig"
    folder.mkdir(parents=True)
    path = folder / "TraderPlusGarageConfig.json"
    path.write_text(GARAGE_TEXT, encoding="utf-8")
    return _client(sample_config), path


def _popup_save(client, **edits) -> None:
    """What GarageNpcPopup sends: every field, the untouched ones as shown."""
    [npc] = client.get("/api/map-editor/garage").json()["garage_npcs"]
    body = {"class_name": npc["class_name"],
            **{k: npc[k] for k in ("game_x", "game_y", "game_z",
                                   "park_game_x", "park_game_y", "park_game_z",
                                   "orientation", "park_orientation")}}
    resp = client.put("/api/map-editor/garage/0", json={**body, **edits})
    assert resp.status_code == 200, resp.text


def test_a_popup_save_that_changes_nothing_leaves_the_file_as_it_was(sample_config, tmp_mission_dir):
    """F75/F10 on a JSON layer: the payload rounds positions to 2 decimals
    and orientations to 1, and the popup sends those back for every field."""
    client, path = _garage_client(sample_config)

    _popup_save(client)

    assert path.read_text(encoding="utf-8") == GARAGE_TEXT


def test_a_popup_save_writes_only_the_field_that_was_edited(sample_config, tmp_mission_dir):
    client, path = _garage_client(sample_config)

    _popup_save(client, game_y=215.5)

    changed = [(a.strip(), b.strip()) for a, b in
               zip(GARAGE_TEXT.splitlines(), path.read_text(encoding="utf-8").splitlines()) if a != b]
    assert changed == [("211.8800048828125,", "215.5,")]


def test_a_drag_keeps_the_height_it_did_not_send(sample_config, tmp_mission_dir):
    client, path = _garage_client(sample_config)
    [npc] = client.get("/api/map-editor/garage").json()["garage_npcs"]

    resp = client.put("/api/map-editor/garage/0", json={"lat": npc["lat"] + 1, "lng": npc["lng"]})

    assert resp.status_code == 200, resp.text
    after = path.read_text(encoding="utf-8")
    assert "211.8800048828125," in after and "222.97168," in after


# ---------------------------------------------------------------------------
# A file that cannot be read is an answer, not a 500 (2026 Q4)
# ---------------------------------------------------------------------------

def _traderplus_dir(config):
    d = config.server_dir_path / config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_effect_area_collisions_name_a_file_they_cannot_read(client, tmp_mission_dir):
    # Saved in Windows-1252 by an ANSI editor: not UTF-8.
    (tmp_mission_dir / "cfgEffectArea.json").write_bytes(
        '{"Areas": [{"AreaName": "Rev\xe9", "Data": {}}]}'.encode("cp1252")
    )
    resp = client.get("/api/map-editor/collisions/effectareas")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "collisions": [], "scanned_events": 0, "scanned_positions": 0, "skipped": ["cfgEffectArea.json"],
    }


@pytest.mark.parametrize("content, skipped", [
    ('{"Traders": [{"Position": []}, {"Position": [1, 0, 1]}]}',
     ["TraderPlusGeneralConfig.json: 1 position without a readable x and z"]),
    ('{"Traders": null}', ["TraderPlusGeneralConfig.json"]),
    ('{"Traders": [', ["TraderPlusGeneralConfig.json"]),
])
def test_traderplus_collisions_name_what_they_left_out(client, sample_config, content, skipped):
    (_traderplus_dir(sample_config) / "TraderPlusGeneralConfig.json").write_text(content, encoding="utf-8")
    resp = client.get("/api/map-editor/collisions/traderplus")
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped"] == skipped
    assert resp.json()["collisions"] == []


_HERE = {"lat": -100.0, "lng": 100.0}


@pytest.mark.parametrize("path, body, name, content", [
    # A stray comma from a hand edit. The file still holds every trader.
    ("/api/map-editor/traders", _HERE, "TraderPlusGeneralConfig.json", b'{"Traders": [{"Id": 1,}]}'),
    ("/api/map-editor/trader-objects", {**_HERE, "object_name": "Land_Barrel"},
     "TraderPlusGeneralConfig.json", b'{"Traders": null, "Licences": ["Car"]}'),
    ("/api/map-editor/safezones", {**_HERE, "radius": 100}, "TraderPlusSafeZoneConfig.json",
     b'{"SafeAreaLocation": [{"SafeZoneStatut": "Zone",}]}'),
    ("/api/map-editor/safezones", {**_HERE, "radius": 100}, "TraderPlusSafeZoneConfig.json",
     b'{"SafeAreaLocation": null, "EnableAfkDisconnect": 1}'),
    ("/api/map-editor/garage", _HERE, "TraderPlusGarageConfig.json", b'{"NPCs": [{"ClassName": "X",}]}'),
    ("/api/map-editor/garage", _HERE, "TraderPlusGarageConfig.json", b'{"NPCs": null, "VehicleMustHaveLock": 1}'),
])
def test_nothing_is_added_to_a_traderplus_file_that_could_not_be_read(
    client, sample_config, path, body, name, content
):
    target = _traderplus_dir(sample_config) / name
    target.write_bytes(content)

    # The layer itself still answers (empty), so the map opens.
    listing = path.replace("trader-objects", "traders")
    assert client.get(listing).status_code == 200

    resp = client.post(path, json=body)
    assert resp.status_code == 400, resp.text
    assert name in resp.json()["detail"]
    assert target.read_bytes() == content


@pytest.mark.parametrize("content", [b'{"IDs": null}', b'{"IDs": [{"Id": "x\xe9"}]}', b'{"IDs": ['])
def test_trader_ids_are_empty_for_a_file_that_cannot_be_read(client, sample_config, content):
    (_traderplus_dir(sample_config) / "TraderPlusIDsConfig.json").write_bytes(content)
    resp = client.get("/api/map-editor/trader-ids")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ids": []}


def test_territory_collisions_name_a_file_they_cannot_read(client, tmp_mission_dir):
    env = tmp_mission_dir / "env"
    env.mkdir(exist_ok=True)
    (env / "wolf_territories.xml").write_bytes(
        b'<territory_type><territory><zone name="A" x="1" z="0" r=""/></territory></territory_type>'
    )
    resp = client.get("/api/map-editor/collisions/territories")
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped"] == ["wolf_territories.xml"]


def test_an_event_edit_keeps_the_comments_in_events_xml(sample_config, tmp_mission_dir):
    """The map editor saves the whole of db/events.xml for one changed field."""
    note = "<!-- raised for the weekend event -->"
    start = "<!-- START MOD: @user:extra -->"
    end = "<!-- END MOD: @user:extra -->"
    events = tmp_mission_dir / "db" / "events.xml"
    events.write_text(
        "<events>\n"
        f"    {note}\n"
        '    <event name="AnimalBear">\n'
        "        <nominal>5</nominal><min>2</min><max>8</max><lifetime>180</lifetime>\n"
        "        <restock>0</restock><saferadius>200</saferadius><distanceradius>0</distanceradius>\n"
        "        <cleanupradius>0</cleanupradius><active>1</active>\n"
        "    </event>\n"
        f"    {start}\n"
        '    <event name="ExtraEvent">\n'
        "        <nominal>1</nominal><min>1</min><max>1</max><lifetime>180</lifetime>\n"
        "        <restock>0</restock><saferadius>0</saferadius><distanceradius>0</distanceradius>\n"
        "        <cleanupradius>0</cleanupradius><active>1</active>\n"
        "    </event>\n"
        f"    {end}\n"
        "</events>\n",
        encoding="utf-8",
    )
    client = _client(sample_config)

    resp = client.put("/api/map-editor/events/AnimalBear", json={"nominal": 7})
    assert resp.status_code == 200, resp.text

    content = events.read_text()
    assert "<nominal>7</nominal>" in content
    for comment in (note, start, end):
        assert comment in content


@pytest.mark.parametrize("body", [{"nominal": 9, "max": "abc"}, {"min": None}, {"deletable": "yes"}, {"lifetime": [1]}])
def test_an_event_number_that_is_not_one_is_a_400_and_changes_nothing(seeded_client, body):
    """Each was a 500 from update_event's int(), after earlier fields applied."""
    name = _seed_spawn(seeded_client)
    before = seeded_client.get(f"/api/map-editor/events/{name}").json()

    resp = seeded_client.put(f"/api/map-editor/events/{name}", json=body)

    assert resp.status_code == 400, resp.text
    assert "must be a whole number" in resp.json()["detail"]
    assert seeded_client.get(f"/api/map-editor/events/{name}").json() == before
