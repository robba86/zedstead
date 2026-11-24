"""The admin's own place names in the map editor (plans/87).

The map editor adds, changes and removes them in `locations_<terrain>.json` in
the user's locations dir, through `app.maps.locations`. They are shown beside
the world-config names, which an own name hides by name or nearness. The file
exports and imports, so it moves to another Zedstead. A stored file that cannot be
read refuses every write and is left as it is.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.maps import locations
from app.shared import paths
from tests.test_web_map_editor import _login, web_app  # noqa: F401  (fixture)

BASE = "/api/map-editor"


def _own_file(terrain="chernarusplus"):
    return paths.locations_dir() / f"locations_{terrain}.json"


def _write_own(entries, terrain="chernarusplus"):
    path = _own_file(terrain)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# app.maps.locations
# ---------------------------------------------------------------------------

def test_own_names_are_added_to_the_world_names_and_hide_the_ones_they_stand_for(monkeypatch):
    world = [
        {"name": "Bigtown", "x": 1000.0, "z": 1000.0, "type": "City"},
        {"name": "Hamlet", "x": 5000.0, "z": 5000.0, "type": "Village"},
        {"name": "Farm", "x": 9000.0, "z": 9000.0, "type": "Local"},
    ]
    monkeypatch.setattr("app.maps.world_names.world_names", lambda server_dir, terrain: world)
    _write_own([
        {"name": "bigtown", "x": 1.0, "z": 1.0, "type": "City"},  # same name
        {"name": "Old Mill", "x": 5050.0, "z": 5000.0, "type": "Local"},  # 50 m from Hamlet
    ])
    places = locations.load_locations("chernarusplus", "/srv")
    assert [(p.name, p.own) for p in places] == [("bigtown", True), ("Old Mill", True), ("Farm", False)]


def test_an_unreadable_own_file_shows_the_world_names_and_refuses_writes(monkeypatch):
    monkeypatch.setattr(
        "app.maps.world_names.world_names",
        lambda server_dir, terrain: [{"name": "Farm", "x": 9.0, "z": 9.0, "type": "Local"}],
    )
    path = _own_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert [p.name for p in locations.load_locations("chernarusplus", "/srv")] == ["Farm"]
    with pytest.raises(locations.LocationFileUnreadable):
        locations.change_own_locations("chernarusplus", lambda places: places)
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("entry, problem", [
    ({"x": 1, "z": 1, "type": "City"}, "needs a name"),
    ({"name": "A", "x": 1, "z": 1, "type": "Castle"}, "Unknown type"),
    ({"name": "A", "x": "1", "z": 1, "type": "City"}, "x must be a number"),
    ({"name": "A", "x": 1, "z": 99999, "type": "City"}, "outside the map"),
    ({"name": "A" * 81, "x": 1, "z": 1, "type": "City"}, "at most"),
    # Each was a TypeError or OverflowError (500) before it was a refusal.
    ({"name": "A", "x": 1, "z": 1, "type": []}, "Unknown type"),
    ({"name": "A", "x": 10**400, "z": 1, "type": "City"}, "x must be a number"),
])
def test_an_import_with_a_bad_place_writes_nothing(entry, problem):
    with pytest.raises(locations.LocationFileError, match=problem):
        locations.import_own_locations("chernarusplus", [entry], replace=False)
    assert not _own_file().exists()


def test_import_merges_skips_duplicates_and_replaces_on_request():
    _write_own([{"name": "Old Mill", "x": 10.0, "z": 20.0, "type": "Local"}])
    data = [
        {"name": "old mill", "x": 10.0, "z": 20.0, "type": "Local"},
        {"name": "Lake", "x": 30.0, "z": 40.0, "type": "Marine", "label": "Ozero"},
    ]
    assert locations.import_own_locations("chernarusplus", data, replace=False) == (1, 1)
    assert [p.name for p in locations.load_own_locations("chernarusplus")] == ["Old Mill", "Lake"]
    assert locations.import_own_locations("chernarusplus", data[1:], replace=True) == (1, 0)
    assert json.loads(locations.export_own_locations("chernarusplus")) == [
        {"name": "Lake", "x": 30.0, "z": 40.0, "type": "Marine", "label": "Ozero"},
    ]


def test_a_bad_terrain_name_reads_and_writes_nothing():
    assert locations.load_own_locations("../../x") == []
    with pytest.raises(locations.LocationFileError):
        locations.change_own_locations("../../x", lambda places: places)


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------

def test_add_rename_move_and_remove_an_own_name(web_app):  # noqa: F811
    with TestClient(web_app) as client:
        _login(client)
        assert client.post(f"{BASE}/locations/own", json={"lat": -100, "lng": 100, "name": "Old Mill"}).status_code == 200
        served = client.get(f"{BASE}/locations").json()
        own = [p for p in served if p.get("own")]
        assert [(p["name"], p["type"], p["index"]) for p in own] == [("Old Mill", "Local", 0)]

        r = client.put(f"{BASE}/locations/own/0", json={"name": "New Mill", "type": "Village", "lat": 0.77, "lng": 10})
        assert r.status_code == 200
        stored = json.loads(_own_file().read_text(encoding="utf-8"))
        assert [(p["name"], p["type"]) for p in stored] == [("New Mill", "Village")]

        assert client.put(f"{BASE}/locations/own/0", json={"type": "Castle"}).status_code == 400
        assert client.delete(f"{BASE}/locations/own/5").status_code == 404
        assert client.delete(f"{BASE}/locations/own/0").status_code == 200
        assert json.loads(_own_file().read_text(encoding="utf-8")) == []


def test_a_write_from_a_stale_list_is_refused(web_app):  # noqa: F811
    """routes._own_place_at: the index alone would hit the wrong place after
    another tab removed one before it, so `expect` names the place meant."""
    _write_own([
        {"name": "Old Mill", "x": 10.0, "z": 20.0, "type": "Local"},
        {"name": "Ruin", "x": 30.0, "z": 40.0, "type": "Local"},
    ])
    with TestClient(web_app) as client:
        _login(client)
        assert client.delete(f"{BASE}/locations/own/0?expect=Ruin").status_code == 409
        assert client.put(f"{BASE}/locations/own/1?expect=Old%20Mill", json={"type": "Village"}).status_code == 409
        assert len(json.loads(_own_file().read_text(encoding="utf-8"))) == 2
        assert client.delete(f"{BASE}/locations/own/1?expect=Ruin").status_code == 200
        assert [p["name"] for p in json.loads(_own_file().read_text(encoding="utf-8"))] == ["Old Mill"]


def test_export_then_import_moves_the_file(web_app):  # noqa: F811
    _write_own([{"name": "Old Mill", "x": 10.0, "z": 20.0, "type": "Local"}])
    with TestClient(web_app) as client:
        _login(client)
        r = client.get(f"{BASE}/locations/export")
        assert r.status_code == 200
        assert "locations_chernarusplus.json" in r.headers["content-disposition"]
        exported = r.json()
        _own_file().unlink()
        r = client.post(f"{BASE}/locations/import", json={"places": exported, "replace": False})
        assert r.json() == {"added": 1, "skipped": 0}
        assert client.post(f"{BASE}/locations/import?terrain=nowhere", json={"places": []}).status_code == 400
    assert json.loads(_own_file().read_text(encoding="utf-8")) == exported


def test_a_stored_file_that_cannot_be_read_is_409_and_kept(web_app):  # noqa: F811
    path = _own_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2", encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        assert client.post(f"{BASE}/locations/own", json={"lat": -100, "lng": 100, "name": "A"}).status_code == 409
        assert client.get(f"{BASE}/locations/export").status_code == 409
    assert path.read_text(encoding="utf-8") == "[1, 2"


def test_a_viewer_cannot_change_own_names(web_app):  # noqa: F811
    web_app.state.ctx.users.add("looker", "lookpass", "viewer")
    with TestClient(web_app) as client:
        assert client.post("/api/auth/login", json={"username": "looker", "password": "lookpass"}).status_code == 200
        assert client.post(f"{BASE}/locations/own", json={"lat": -100, "lng": 100, "name": "A"}).status_code == 403
        assert client.post(f"{BASE}/locations/import", json={"places": []}).status_code == 403
    assert not _own_file().exists()
