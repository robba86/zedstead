"""The Map Editor's Items tab: lasting placement of a mod's class.

Two ways, both through /api/map-editor: a new event in events.xml that
spawns the class (`POST /events` with ``child``, `GET /events-for-class`),
and Zedstead's own Object Spawner file listed in cfggameplay.json
(`/object-spawner`, `app/spawn_editor/object_spawner.py`), placed at the
ground's height from `terrain_map.height_at`.
"""

from __future__ import annotations

import gzip
import json
from array import array

import pytest
from fastapi.testclient import TestClient

from app.maps.terrain_map import Heights, encode_heights, height_at
from app.spawn_editor.converters import game_to_latlng
from app.spawn_editor.object_spawner import OWN_FILE
from tests.test_web_map_editor import _login, web_app  # noqa: F401  (fixture)

TERRAIN = "chernarusplus"

GAMEPLAY = '{\n    "version": 123,\n    "WorldsData": {\n        "lightingConfig": 0,\n        "objectSpawnersArr": []\n    }\n}\n'


def _latlng(x: float, z: float) -> dict:
    lat, lng = game_to_latlng(x, z, TERRAIN)
    return {"lat": lat, "lng": lng}


def _heights_file(tmp_path, value_at):
    """A heights cache whose grid point (col, row) at 10 m spacing is *value_at(col, row)*."""
    n = 4
    vals = array("f", [float(value_at(c, r)) for r in range(n) for c in range(n)])
    path = tmp_path / "heights.gz"
    path.write_bytes(gzip.compress(encode_heights(Heights(side=n, cell=10.0, values=vals))))
    return path


# ---------------------------------------------------------------------------
# height_at
# ---------------------------------------------------------------------------


def test_height_at_reads_the_grid_between_points(tmp_path):
    gz = _heights_file(tmp_path, lambda c, r: c + 10 * r)
    assert height_at(gz, 0, 0) == 0.0
    assert height_at(gz, 10, 0) == 1.0  # X runs along a row
    assert height_at(gz, 0, 10) == 10.0  # Z runs across rows, row 0 south
    assert height_at(gz, 15, 15) == 16.5  # bilinear
    assert height_at(gz, 31, 0) is None  # off the grid


def test_height_at_reads_a_broken_file_as_none(tmp_path):
    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"not gzip")
    assert height_at(bad, 0, 0) is None


# ---------------------------------------------------------------------------
# A new event that spawns the class
# ---------------------------------------------------------------------------


def test_new_event_with_a_child_spawns_that_class(web_app, tmp_mission_dir):  # noqa: F811
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/events", json={"name": "ItemMyRifle", "child": "MyMod_Rifle"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["children"] == [
            {"type": "MyMod_Rifle", "min": 1, "max": 1, "lootmin": 0, "lootmax": 0}
        ]
        events = (tmp_mission_dir / "db" / "events.xml").read_text(encoding="utf-8")
        assert 'type="MyMod_Rifle"' in events
        spawns = (tmp_mission_dir / "cfgeventspawns.xml").read_text(encoding="utf-8")
        assert 'name="ItemMyRifle"' in spawns

        found = client.get("/api/map-editor/events-for-class/mymod_rifle")
        assert found.json() == [{"name": "ItemMyRifle", "spawn_count": 0}]

        point = client.post("/api/map-editor/spawns/ItemMyRifle", json=_latlng(7500, 7500))
        assert point.status_code == 200, point.text
        assert client.get("/api/map-editor/events-for-class/MyMod_Rifle").json()[0]["spawn_count"] == 1


def test_a_child_is_never_added_to_an_existing_event(web_app, tmp_mission_dir):  # noqa: F811
    with TestClient(web_app) as client:
        _login(client)
        assert client.post("/api/map-editor/events", json={"name": "ItemOld"}).status_code == 200
        resp = client.post("/api/map-editor/events", json={"name": "ItemOld", "child": "MyMod_Rifle"})
        assert resp.status_code == 409
        assert "MyMod_Rifle" not in (tmp_mission_dir / "db" / "events.xml").read_text(encoding="utf-8")


def test_a_child_that_is_not_a_class_name_is_refused(web_app, tmp_mission_dir):  # noqa: F811
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/events", json={"name": "ItemBad", "child": 'x" evil="1'})
        assert resp.status_code == 400
        assert "ItemBad" not in (tmp_mission_dir / "db" / "events.xml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Object Spawner
# ---------------------------------------------------------------------------


@pytest.fixture()
def flat_ground(monkeypatch, tmp_path):
    """Terrain heights of 42.5 m everywhere on a 10 km grid."""
    n = 3
    vals = array("f", [42.5] * (n * n))
    gz = tmp_path / "flat.gz"
    gz.write_bytes(gzip.compress(encode_heights(Heights(side=n, cell=8000.0, values=vals))))
    monkeypatch.setattr("app.maps.terrain_map.terrain_heights", lambda *a, **k: gz)
    return gz


@pytest.fixture()
def no_heights(monkeypatch):
    monkeypatch.setattr("app.maps.terrain_map.terrain_heights", lambda *a, **k: None)


def test_an_object_lands_on_the_ground_and_its_file_is_listed(web_app, tmp_mission_dir, flat_ground):  # noqa: F811
    gameplay = tmp_mission_dir / "cfggameplay.json"
    gameplay.write_text(GAMEPLAY, encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/object-spawner", json={**_latlng(7500, 7600), "name": "MyMod_Crate"})
        assert resp.status_code == 200, resp.text
        row = resp.json()
        assert row["name"] == "MyMod_Crate"
        assert row["y"] == 42.5
        assert row["listed"] is True

        own = json.loads((tmp_mission_dir / OWN_FILE).read_text(encoding="utf-8"))
        (obj,) = own["Objects"]
        assert obj["name"] == "MyMod_Crate"
        assert obj["pos"][0] == pytest.approx(7500, abs=0.01)
        assert obj["pos"][1] == 42.5
        assert obj["pos"][2] == pytest.approx(7600, abs=0.01)
        assert obj["ypr"] == [0.0, 0.0, 0.0]

        listed = json.loads(gameplay.read_text(encoding="utf-8"))
        assert listed["WorldsData"]["objectSpawnersArr"] == [OWN_FILE]
        assert listed["WorldsData"]["lightingConfig"] == 0
        assert listed["version"] == 123

        # A second object does not list the file twice.
        assert client.post(
            "/api/map-editor/object-spawner", json={**_latlng(7000, 7000), "name": "MyMod_Crate"}
        ).status_code == 200
        assert json.loads(gameplay.read_text(encoding="utf-8"))["WorldsData"]["objectSpawnersArr"] == [OWN_FILE]

        data = client.get("/api/map-editor/object-spawner").json()
        assert [o["name"] for o in data["objects"]] == ["MyMod_Crate", "MyMod_Crate"]
        assert data["listed"] is True
        # sample_config's serverDZ.cfg does not turn cfggameplay.json on.
        assert data["gameplay_on"] in (False, None)


def test_an_object_without_heights_needs_a_typed_y(web_app, tmp_mission_dir, no_heights):  # noqa: F811
    (tmp_mission_dir / "cfggameplay.json").write_text(GAMEPLAY, encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        body = {**_latlng(7500, 7500), "name": "MyMod_Crate"}
        resp = client.post("/api/map-editor/object-spawner", json=body)
        assert resp.status_code == 400
        assert "Y" in resp.json()["detail"]
        assert not (tmp_mission_dir / OWN_FILE).exists()

        resp = client.post("/api/map-editor/object-spawner", json={**body, "y": 120.25, "yaw": 90})
        assert resp.status_code == 200, resp.text
        obj = json.loads((tmp_mission_dir / OWN_FILE).read_text(encoding="utf-8"))["Objects"][0]
        assert obj["pos"][1] == 120.25
        assert obj["ypr"][0] == 90


def test_an_object_needs_cfggameplay_json(web_app, tmp_mission_dir, flat_ground):  # noqa: F811
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/object-spawner", json={**_latlng(7500, 7500), "name": "MyMod_Crate"})
        assert resp.status_code == 409
        assert "cfggameplay.json" in resp.json()["detail"]
        assert not (tmp_mission_dir / OWN_FILE).exists()


def test_an_object_name_must_be_a_class_name(web_app, tmp_mission_dir, flat_ground):  # noqa: F811
    (tmp_mission_dir / "cfggameplay.json").write_text(GAMEPLAY, encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/object-spawner", json={**_latlng(7500, 7500), "name": "../x"})
        assert resp.status_code == 400
        assert not (tmp_mission_dir / OWN_FILE).exists()


def test_an_object_off_the_map_is_refused(web_app, tmp_mission_dir, flat_ground):  # noqa: F811
    (tmp_mission_dir / "cfggameplay.json").write_text(GAMEPLAY, encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/object-spawner", json={**_latlng(99999, 10), "name": "MyMod_Crate"})
        assert resp.status_code == 400


def test_remove_checks_the_name_it_showed_and_keeps_other_keys(web_app, tmp_mission_dir):  # noqa: F811
    (tmp_mission_dir / "cfggameplay.json").write_text(GAMEPLAY, encoding="utf-8")
    own = tmp_mission_dir / OWN_FILE
    own.write_text(
        json.dumps({"Objects": [
            {"name": "A", "pos": [7500, 10, 7500], "ypr": [0, 0, 0]},
            {"name": "B", "pos": [7600, 10, 7600], "ypr": [0, 0, 0], "scale": 2, "customString": "keep"},
        ]}, indent=4),
        encoding="utf-8",
    )
    with TestClient(web_app) as client:
        _login(client)
        assert client.delete("/api/map-editor/object-spawner/0?expect=B").status_code == 409
        assert client.delete("/api/map-editor/object-spawner/5?expect=A").status_code == 404
        assert client.delete("/api/map-editor/object-spawner/0?expect=A").status_code == 200
        left = json.loads(own.read_text(encoding="utf-8"))["Objects"]
        assert left == [{"name": "B", "pos": [7600, 10, 7600], "ypr": [0, 0, 0], "scale": 2, "customString": "keep"}]


def test_an_unreadable_own_file_is_kept(web_app, tmp_mission_dir, flat_ground):  # noqa: F811
    (tmp_mission_dir / "cfggameplay.json").write_text(GAMEPLAY, encoding="utf-8")
    own = tmp_mission_dir / OWN_FILE
    own.write_text("{ not json", encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        assert "error" in client.get("/api/map-editor/object-spawner").json()
        resp = client.post("/api/map-editor/object-spawner", json={**_latlng(7500, 7500), "name": "MyMod_Crate"})
        assert resp.status_code == 409
        assert own.read_text(encoding="utf-8") == "{ not json"


def test_the_gameplay_switch_writes_server_dz_cfg(web_app, tmp_mission_dir, sample_config):  # noqa: F811
    from app.parsers.servercfg import read_cfg_value

    cfg = sample_config.server_cfg_path
    with TestClient(web_app) as client:
        _login(client)
        assert client.put("/api/map-editor/object-spawner/gameplay-file", json={"on": "yes"}).status_code == 400
        resp = client.put("/api/map-editor/object-spawner/gameplay-file", json={"on": True})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"gameplay_on": True}
        assert read_cfg_value(cfg, "enableCfgGameplayFile") == "1"
        assert client.get("/api/map-editor/object-spawner").json()["gameplay_on"] is True

        resp = client.put("/api/map-editor/object-spawner/gameplay-file", json={"on": False})
        assert resp.json() == {"gameplay_on": False}
        assert read_cfg_value(cfg, "enableCfgGameplayFile") == "0"


def test_disable_moves_an_object_to_a_file_dayz_never_reads_and_back(web_app, tmp_mission_dir):  # noqa: F811
    from app.spawn_editor.object_spawner import DISABLED_FILE

    gameplay = tmp_mission_dir / "cfggameplay.json"
    gameplay.write_text(GAMEPLAY, encoding="utf-8")
    own, off = tmp_mission_dir / OWN_FILE, tmp_mission_dir / DISABLED_FILE
    own.write_text(
        json.dumps({"Objects": [
            {"name": "A", "pos": [7500, 10, 7500], "ypr": [0, 0, 0]},
            {"name": "B", "pos": [7600, 10, 7600], "ypr": [45, 0, 0], "scale": 2},
        ]}),
        encoding="utf-8",
    )
    with TestClient(web_app) as client:
        _login(client)
        assert client.post("/api/map-editor/object-spawner/1/toggle?expect=A").status_code == 409
        resp = client.post("/api/map-editor/object-spawner/1/toggle?expect=B")
        assert resp.status_code == 200, resp.text
        assert resp.json()["disabled"] is True and resp.json()["index"] == 0
        assert [o["name"] for o in json.loads(own.read_text(encoding="utf-8"))["Objects"]] == ["A"]
        assert json.loads(off.read_text(encoding="utf-8"))["Objects"] == [
            {"name": "B", "pos": [7600, 10, 7600], "ypr": [45, 0, 0], "scale": 2}
        ]
        assert DISABLED_FILE not in gameplay.read_text(encoding="utf-8")

        rows = client.get("/api/map-editor/object-spawner").json()["objects"]
        assert [(o["name"], o["disabled"]) for o in rows] == [("A", False), ("B", True)]

        back = client.post("/api/map-editor/object-spawner/0/toggle?expect=B&disabled=true")
        assert back.json()["disabled"] is False and back.json()["index"] == 1
        assert json.loads(off.read_text(encoding="utf-8"))["Objects"] == []
        assert json.loads(own.read_text(encoding="utf-8"))["Objects"][1]["scale"] == 2

        client.post("/api/map-editor/object-spawner/1/toggle?expect=B")
        assert client.delete("/api/map-editor/object-spawner/0?expect=B&disabled=true").status_code == 200
        assert json.loads(off.read_text(encoding="utf-8"))["Objects"] == []


def test_edit_sets_position_and_rotation_and_keeps_other_keys(web_app, tmp_mission_dir):  # noqa: F811
    (tmp_mission_dir / "cfggameplay.json").write_text(GAMEPLAY, encoding="utf-8")
    own = tmp_mission_dir / OWN_FILE
    own.write_text(
        json.dumps({"Objects": [{"name": "A", "pos": [7500, 10, 7500], "ypr": [0, 0, 0], "scale": 2}]}),
        encoding="utf-8",
    )
    body = {"x": 7000.5, "y": 12.25, "z": 7100, "yaw": 90, "pitch": 5, "roll": -3}
    with TestClient(web_app) as client:
        _login(client)
        assert client.put("/api/map-editor/object-spawner/0?expect=B", json=body).status_code == 409
        assert client.put("/api/map-editor/object-spawner/0?expect=A", json={**body, "x": 99999}).status_code == 400
        assert client.put("/api/map-editor/object-spawner/0?expect=A", json={**body, "roll": "x"}).status_code == 400
        resp = client.put("/api/map-editor/object-spawner/0?expect=A", json=body)
        assert resp.status_code == 200, resp.text
        assert (resp.json()["x"], resp.json()["pitch"]) == (7000.5, 5)
        assert json.loads(own.read_text(encoding="utf-8"))["Objects"] == [
            {"name": "A", "pos": [7000.5, 12.25, 7100], "ypr": [90, 5, -3], "scale": 2}
        ]


def test_ground_answers_the_terrain_height_or_404(web_app, tmp_mission_dir, flat_ground):  # noqa: F811
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/object-spawner/ground?x=7500&z=7500").json() == {"y": 42.5}
        assert client.get("/api/map-editor/object-spawner/ground?x=99999&z=7500").status_code == 404
        p = _latlng(7000, 7100)
        got = client.get(f"/api/map-editor/object-spawner/ground?lat={p['lat']}&lng={p['lng']}").json()
        assert got == {"x": pytest.approx(7000, abs=0.01), "z": pytest.approx(7100, abs=0.01), "y": 42.5}
