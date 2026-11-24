"""The map editor served by the web app at /api/map-editor.

Session auth, status/reload, the `mapeditor` WS channel (namespaced pings +
watcher gating), and the lat/lng form of add-player-spawn-position.

The legacy /map-editor ASGI mount and its tests were deleted with the iframe;
what survived of them is test_spa_route_serves_the_spa below.
"""

from __future__ import annotations

import asyncio
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CE_1_29, bohemia_ce, ce_lines

PASSWORD = "testpass123"


async def _fake_watch_mission_files(*args, **kwargs) -> None:
    await asyncio.sleep(3600)


@pytest.fixture()
def web_app(sample_config, tmp_mission_dir, monkeypatch):
    from app.web import server
    from app.web.auth import hash_password

    monkeypatch.setattr(
        "app.spawn_editor.watcher.watch_mission_files", _fake_watch_mission_files
    )

    db_dir = tmp_mission_dir / "db"
    (db_dir / "events.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<events>\n</events>\n',
        encoding="utf-8",
    )
    (tmp_mission_dir / "cfgeventspawns.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<eventposdef>\n</eventposdef>\n",
        encoding="utf-8",
    )
    (db_dir / "cfgPlayerSpawnPoints.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <playerspawnpoints>
              <fresh>
                <generator_posbubbles>
                  <pos x="7500.0" z="7500.0" />
                </generator_posbubbles>
                <group name="TestGroup">
                  <pos x="7500.0" z="7500.0" />
                </group>
              </fresh>
            </playerspawnpoints>
        """),
        encoding="utf-8",
    )

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


# ---------------------------------------------------------------------------
# Auth + parity with the mounted editor
# ---------------------------------------------------------------------------

def test_editor_api_requires_auth(web_app):
    with TestClient(web_app) as client:
        assert client.get("/api/map-editor/spawns").status_code == 401
        assert client.get("/api/map-editor/map-info").status_code == 401
        assert client.get("/api/map-editor/status").status_code == 401


def test_map_info_carries_the_world_and_no_shipped_image(web_app):
    """Bounds are the world's; with no upload a map has no image (plans/69)."""
    from app.spawn_editor.converters import get_map_bounds

    with TestClient(web_app) as client:
        _login(client)
        info = client.get("/api/map-editor/map-info").json()
        namalsk = client.get("/api/map-editor/map-info?terrain=namalsk").json()
        image = client.get("/api/map-editor/map-image?terrain=namalsk")
    assert info["bounds"] == get_map_bounds("chernarusplus")
    assert "image_bounds" not in info
    assert (info["image_cleared"], info["credit"], info["lakes"]) == (False, None, [])
    assert namalsk["has_image"] is False and namalsk["image_own"] is None
    assert (namalsk["image_cleared"], namalsk["credit"]) == (False, None)
    assert image.status_code == 404


class _FakeImageResponse:
    """A streamed `requests` answer for the cleared-image download."""

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.body


def test_a_cleared_map_image_is_downloaded_once_with_its_credit(web_app, monkeypatch):
    """Banov's image comes from zedstead.com's fixed URL, once, with KubeloLive's credit (plans/65)."""
    import requests

    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 64
    asked = []

    def fake_get(url, **kwargs):
        asked.append((url, kwargs.get("timeout")))
        return _FakeImageResponse(webp)

    monkeypatch.setattr(requests, "get", fake_get)
    with TestClient(web_app) as client:
        _login(client)
        info = client.get("/api/map-editor/map-info?terrain=banov").json()
        first = client.get("/api/map-editor/map-image?terrain=banov")
        second = client.get("/api/map-editor/map-image?terrain=banov")
    assert (info["has_image"], info["image_cleared"], info["image_own"]) == (True, True, None)
    assert info["credit"]["text"] == "Banov & Banov Frost created by KubeloLive"
    # A point in each of Banov's lakes, for the terrain map to fill.
    assert info["lakes"] and all(len(p) == 2 for p in info["lakes"])
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.content == webp
    assert asked == [("https://zedstead.com/maps/banov.webp", 60)]


def test_a_cleared_download_that_is_not_a_jpeg_is_not_kept(web_app, monkeypatch):
    """An HTML error page with status 200 is never served as the map."""
    import requests

    from app.maps import map_data

    monkeypatch.setattr(requests, "get", lambda url, **k: _FakeImageResponse(b"<html>error</html>"))
    with TestClient(web_app) as client:
        _login(client)
        image = client.get("/api/map-editor/map-image?terrain=banovfrost")
    assert image.status_code == 404
    assert map_data.cleared_image_cached("banovfrost") is None
    assert not list(map_data.cleared_images_dir().glob("*"))


def test_the_terrain_map_comes_from_the_servers_world_file(web_app, tmp_mission_dir, tmp_path, monkeypatch):
    """plans/70: has_terrain only with a world file, and the route serves its heights."""
    from app.maps import terrain_map
    from tests.test_terrain_map import coast, world_pbo

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    server_dir = tmp_mission_dir.parent.parent
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/map-info").json()["has_terrain"] is False
        assert client.get("/api/map-editor/terrain-heights").status_code == 404
        world_pbo(server_dir, "chernarusplus", coast())
        assert client.get("/api/map-editor/map-info").json()["has_terrain"] is True
        resp = client.get("/api/map-editor/terrain-heights")
        other = client.get("/api/map-editor/terrain-heights?terrain=enoch")
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.content[:4] == b"DZTH"  # the client inflated it
    assert other.status_code == 404


def test_a_downloaded_maps_terrain_is_read_from_the_workshop(web_app, sample_config, tmp_path, monkeypatch):
    """The Maps page's Download only leaves the map mod in the Workshop cache, not the server dir.

    Namalsk is put on registry.TERRAIN_LAYER_MAPS here, as a community map
    would be once its maker agrees: the search behind the allowlist still works.
    """
    from app.maps import registry, terrain_map

    monkeypatch.setattr(registry, "TERRAIN_LAYER_MAPS", registry.TERRAIN_LAYER_MAPS | {"namalsk"})
    from app.shared.seed_data import WORKSHOP_MAPS
    from tests.test_terrain_map import coast, pbo_bytes, wrp_bytes

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    sample_config.workshop_content_dir = str(tmp_path / "workshop")
    addons = tmp_path / "workshop" / WORKSHOP_MAPS["Namalsk"]["mods"]["@Namalsk Island"] / "addons"
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/map-info?terrain=namalsk").json()["has_terrain"] is False
        addons.mkdir(parents=True)
        (addons / "invented.pbo").write_bytes(pbo_bytes({"namalsk.wrp": wrp_bytes(coast())}))
        assert client.get("/api/map-editor/map-info?terrain=namalsk").json()["has_terrain"] is True
        assert client.get("/api/map-editor/terrain-heights?terrain=namalsk").status_code == 200


def test_map_info_says_why_a_map_has_no_terrain(web_app, tmp_mission_dir):
    """An encrypted world file (Sakhal's) is not the same as a map that is not downloaded."""
    server_dir = tmp_mission_dir.parent.parent
    with TestClient(web_app) as client:
        _login(client)
        before = client.get("/api/map-editor/map-info?terrain=sakhal").json()
        addons = server_dir / "sakhal" / "addons"
        addons.mkdir(parents=True)
        (addons / "worlds_sakhal.ebo").write_bytes(b"invented")
        after = client.get("/api/map-editor/map-info?terrain=sakhal").json()
    assert (before["has_terrain"], before["terrain_problem"]) == (False, "missing")
    assert (after["has_terrain"], after["terrain_problem"]) == (False, "encrypted")
    assert (after["terrain_source"], after["has_roads"]) == (None, False)


def test_an_encrypted_maps_terrain_comes_from_its_navmesh_without_roads(web_app, tmp_mission_dir, tmp_path, monkeypatch):
    """plans/74: Sakhal's world file is encrypted, its navmesh is not; it has no roads."""
    from app.maps import registry, terrain_map
    from tests.test_navmesh_map import ground_tiles, nav_pbo, navmesh_bytes
    from tests.test_terrain_map import coast, world_pbo

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    monkeypatch.setitem(registry.MAP_SIZES, "sakhal", 120)
    monkeypatch.setattr(terrain_map, "MAX_SIDE", 12)
    server_dir = tmp_mission_dir.parent.parent
    addons = server_dir / "sakhal" / "addons"
    addons.mkdir(parents=True)
    (addons / "worlds_sakhal.ebo").write_bytes(b"invented")
    nav_pbo(addons, "sakhal", navmesh_bytes(ground_tiles(), 2, 2))
    world_pbo(server_dir, "chernarusplus", coast())
    with TestClient(web_app) as client:
        _login(client)
        sakhal = client.get("/api/map-editor/map-info?terrain=sakhal").json()
        cherno = client.get("/api/map-editor/map-info").json()
        heights = client.get("/api/map-editor/terrain-heights?terrain=sakhal")
        roads = client.get("/api/map-editor/roads?terrain=sakhal")
    assert {k: sakhal[k] for k in ("has_terrain", "terrain_source", "terrain_problem", "has_roads")} == {
        "has_terrain": True, "terrain_source": "navmesh", "terrain_problem": "encrypted", "has_roads": False,
    }
    assert {k: cherno[k] for k in ("has_terrain", "terrain_source", "terrain_problem", "has_roads")} == {
        "has_terrain": True, "terrain_source": "world", "terrain_problem": None, "has_roads": True,
    }
    assert heights.status_code == 200
    assert heights.content[:4] == b"DZTH"
    assert roads.status_code == 404


def test_a_navmesh_that_fails_to_read_turns_the_terrain_off_with_the_reason(web_app, tmp_mission_dir, tmp_path, monkeypatch):
    """A navmesh Zedstead cannot read: the 404 names the cause, and /map-info then offers no terrain."""
    from app.maps import registry, terrain_map
    from tests.test_navmesh_map import ground_tiles, nav_pbo, navmesh_bytes

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    monkeypatch.setitem(registry.MAP_SIZES, "sakhal", 120)
    monkeypatch.setattr(terrain_map, "_failed", {})
    addons = tmp_mission_dir.parent.parent / "sakhal" / "addons"
    addons.mkdir(parents=True)
    (addons / "worlds_sakhal.ebo").write_bytes(b"invented")
    nav_pbo(addons, "sakhal", navmesh_bytes(ground_tiles(), 2, 2, version=5))
    with TestClient(web_app) as client:
        _login(client)
        before = client.get("/api/map-editor/map-info?terrain=sakhal").json()
        heights = client.get("/api/map-editor/terrain-heights?terrain=sakhal")
        after = client.get("/api/map-editor/map-info?terrain=sakhal").json()
    assert before["has_terrain"] is True
    assert heights.status_code == 404
    assert heights.json()["detail"] == "This map's navmesh is a version Zedstead cannot read yet"
    assert (after["has_terrain"], after["terrain_problem"]) == (False, "navmesh_unreadable")


@pytest.mark.parametrize("terrain", ["namalsk", "deerisle", "esseker", "bitterroot", "takistanplus"])
def test_a_community_map_has_no_terrain_layer_and_no_file_is_read(web_app, tmp_mission_dir, monkeypatch, terrain):
    """Off registry.heights_allowed: /map-info says not_available, both routes 404, no reader runs."""
    from app.maps import navmesh_map, road_map, terrain_map
    from tests.test_terrain_map import coast, world_pbo

    world_pbo(tmp_mission_dir.parent.parent, terrain, coast())  # a readable world file is there
    calls = []
    for module, name in [
        (terrain_map, "world_file"), (terrain_map, "_no_world_file"), (terrain_map, "terrain_heights"),
        (road_map, "terrain_roads"), (navmesh_map, "navmesh_file"),
    ]:
        monkeypatch.setattr(module, name, lambda *a, _n=name, **k: calls.append(_n))
    with TestClient(web_app) as client:
        _login(client)
        info = client.get(f"/api/map-editor/map-info?terrain={terrain}").json()
        heights = client.get(f"/api/map-editor/terrain-heights?terrain={terrain}")
        roads = client.get(f"/api/map-editor/roads?terrain={terrain}")
    assert {k: info[k] for k in ("has_terrain", "terrain_source", "terrain_problem", "has_roads")} == {
        "has_terrain": False, "terrain_source": None, "terrain_problem": "not_available", "has_roads": False,
    }
    assert (heights.status_code, roads.status_code) == (404, 404)
    assert heights.json()["detail"] == "The terrain layer is not available for this map."
    assert calls == []


@pytest.mark.parametrize("terrain", ["banov", "banovfrost"])
def test_a_cleared_map_has_world_heights_and_roads(web_app, tmp_mission_dir, tmp_path, monkeypatch, terrain):
    """KubeloLive cleared both maps' heights and roads (plans/65)."""
    from app.maps import road_map, terrain_map
    from tests.test_terrain_map import coast, world_pbo

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    monkeypatch.setattr(road_map, "terrain_roads", lambda *a, **k: tmp_path / "roads.gz")
    (tmp_path / "roads.gz").write_bytes(b"x")
    world_pbo(tmp_mission_dir.parent.parent, terrain, coast())
    with TestClient(web_app) as client:
        _login(client)
        info = client.get(f"/api/map-editor/map-info?terrain={terrain}").json()
        heights = client.get(f"/api/map-editor/terrain-heights?terrain={terrain}")
        roads = client.get(f"/api/map-editor/roads?terrain={terrain}")
    assert (info["has_terrain"], info["terrain_source"], info["has_roads"]) == (True, "world", True)
    assert info["credit"]["text"].startswith("Banov & Banov Frost")
    assert (heights.status_code, roads.status_code) == (200, 200)


def test_a_maker_grid_terrain_needs_the_world_file_for_roads(web_app, tmp_mission_dir, monkeypatch):
    """With a hosted maker grid (not shipped yet), heights come from it and roads from the world file."""
    from app.maps import maker_heights, registry
    from tests.test_terrain_map import coast, world_pbo

    cleared = registry.ClearedMap(registry._KUBELO, heights_url="https://zedstead.com/maps/banov.asc.gz", roads=True)
    monkeypatch.setitem(registry.CLEARED_MAPS, "banov", cleared)
    monkeypatch.setattr(maker_heights, "maker_heights", lambda terrain: None)
    with TestClient(web_app) as client:
        _login(client)
        before = client.get("/api/map-editor/map-info?terrain=banov").json()
        world_pbo(tmp_mission_dir.parent.parent, "banov", coast())
        after = client.get("/api/map-editor/map-info?terrain=banov").json()
    assert (before["has_terrain"], before["terrain_source"], before["has_roads"]) == (True, "maker", False)
    assert (after["terrain_source"], after["has_roads"]) == ("maker", True)


def test_the_terrain_map_requires_a_session(web_app):
    with TestClient(web_app) as client:
        assert client.get("/api/map-editor/terrain-heights").status_code == 401


def test_the_roads_come_from_the_same_world_file(web_app, tmp_mission_dir, tmp_path, monkeypatch):
    """plans/70: the roads by kind, gzipped JSON, 404 without a world file."""
    from app.maps import terrain_map
    from tests.test_terrain_map import coast, world_pbo

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    server_dir = tmp_mission_dir.parent.parent
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/roads").status_code == 404
        world_pbo(server_dir, "chernarusplus", coast(), roads=[("dz\\roads\\asf1_25.p3d", [(0.0, 0.0), (25.0, 0.0)])])
        resp = client.get("/api/map-editor/roads")
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.json() == {"roads": {"main": [[0.0, 0.0, 25.0, 0.0]]}}


def test_a_world_file_with_values_no_map_has_is_no_data_not_an_error(web_app, tmp_mission_dir, tmp_path, monkeypatch):
    """plans/68 NB-1: an infinite height or road point answered 500 on every request."""
    from app.maps import terrain_map
    from tests.test_terrain_map import coast, world_pbo

    monkeypatch.setattr(terrain_map.paths, "maps_dir", lambda: tmp_path / "maps")
    grid = coast()
    grid[0][4] = float("inf")
    world_pbo(tmp_mission_dir.parent.parent, "chernarusplus", grid,
              roads=[("dz\\roads\\asf1_25.p3d", [(0.0, 0.0), (float("inf"), 0.0)])])
    with TestClient(web_app) as client:
        _login(client)
        for _ in range(2):
            assert client.get("/api/map-editor/terrain-heights").status_code == 404
            # The one road piece is left out, not the whole road list refused.
            assert client.get("/api/map-editor/roads").status_code == 200


def test_the_roads_require_a_session(web_app):
    with TestClient(web_app) as client:
        assert client.get("/api/map-editor/roads").status_code == 401


# ---------------------------------------------------------------------------
# The admin's own map image (plans/69 Step 4)
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_an_uploaded_image_becomes_the_maps_image(web_app):
    """Served by GET /map-image and laid on the whole world."""
    from app.spawn_editor.converters import get_map_bounds

    with TestClient(web_app) as client:
        _login(client)
        before = client.get("/api/map-editor/map-info?terrain=namalsk").json()
        resp = client.post("/api/map-editor/map-image?terrain=namalsk", content=PNG)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"terrain": "namalsk", "size": len(PNG), "type": "png"}
        info = client.get("/api/map-editor/map-info?terrain=namalsk").json()
        image = client.get("/api/map-editor/map-image?terrain=namalsk")
    assert before["image_own"] is None and before["has_image"] is False
    assert info["image_own"]["size"] == len(PNG)
    assert info["has_image"] is True
    assert info["bounds"] == get_map_bounds("namalsk")
    assert image.status_code == 200 and image.content == PNG


def test_the_preview_upload_is_what_the_maps_page_shows(web_app):
    """The browser sends a small JPEG after the image; /api/maps/thumbnail
    serves it, and the map editor keeps the full image."""
    thumb = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    with TestClient(web_app) as client:
        _login(client)
        early = client.post("/api/map-editor/map-image/thumbnail?terrain=banov", content=thumb)
        client.post("/api/map-editor/map-image?terrain=banov", content=PNG)
        resp = client.post("/api/map-editor/map-image/thumbnail?terrain=banov", content=thumb)
        shown = client.get("/api/maps/thumbnail?terrain=banov")
        image = client.get("/api/map-editor/map-image?terrain=banov")
        unknown = client.post("/api/map-editor/map-image/thumbnail?terrain=nosuchmap", content=thumb)
    assert early.status_code == 400
    assert resp.status_code == 200 and resp.json() == {"terrain": "banov", "size": len(thumb)}
    assert shown.content == thumb
    assert image.content == PNG
    assert unknown.status_code == 404


def test_removing_the_image_leaves_the_map_without_one(web_app):
    with TestClient(web_app) as client:
        _login(client)
        client.post("/api/map-editor/map-image?terrain=chernarusplus", content=PNG)
        assert client.delete("/api/map-editor/map-image?terrain=chernarusplus").status_code == 200
        info = client.get("/api/map-editor/map-info?terrain=chernarusplus").json()
        again = client.delete("/api/map-editor/map-image?terrain=chernarusplus")
    assert info["image_own"] is None
    assert info["has_image"] is False
    assert again.status_code == 404


@pytest.mark.parametrize(
    "query, body, status",
    [
        ("terrain=chernarusplus", b"<html>not an image</html>", 400),
        ("terrain=nosuchmap", PNG, 404),
        ("terrain=..%2F..%2Fconfig", PNG, 404),
        ("", PNG, 422),
    ],
)
def test_an_upload_that_is_not_an_image_or_not_a_map_is_refused(web_app, query, body, status):
    from app.maps import map_data

    with TestClient(web_app) as client:
        _login(client)
        assert client.post(f"/api/map-editor/map-image?{query}", content=body).status_code == status
    assert not map_data.own_images_dir().exists() or not any(map_data.own_images_dir().iterdir())


def test_an_upload_past_the_cap_is_refused_while_it_streams(web_app, monkeypatch):
    from app.maps import map_data

    monkeypatch.setattr(map_data, "MAX_OWN_IMAGE_BYTES", 32)
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post("/api/map-editor/map-image?terrain=chernarusplus", content=PNG)
    assert resp.status_code == 413
    assert map_data.own_image("chernarusplus") is None


def test_the_picker_and_the_upload_agree_on_a_running_community_map(web_app, sample_config, tmp_mission_dir):
    """NEW-5: one list, `routes.editor_terrains`, for GET /maps and the upload.
    A community map zsm does not list is in it while it is the one running."""
    from app.maps import map_data
    from app.maps.registry import known_terrains

    community = tmp_mission_dir.parent / "empty.mycommunitymap"
    (community / "db").mkdir(parents=True)
    for f in ("db/events.xml", "cfgeventspawns.xml"):
        (community / f).write_text((tmp_mission_dir / f).read_text(encoding="utf-8"), encoding="utf-8")
    cfg = sample_config.server_cfg_path
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("dayzOffline.chernarusplus", "empty.mycommunitymap"),
        encoding="utf-8",
    )
    assert "mycommunitymap" not in known_terrains()
    with TestClient(web_app) as client:
        _login(client)
        picker = {m["terrain"] for m in client.get("/api/map-editor/maps").json()}
        upload = client.post("/api/map-editor/map-image?terrain=mycommunitymap", content=PNG)
        other = client.post("/api/map-editor/map-image?terrain=othercommunitymap", content=PNG)
    assert "mycommunitymap" in picker
    assert upload.status_code == 200, upload.text
    assert map_data.own_image("mycommunitymap") is not None
    assert other.status_code == 404


def test_the_picker_and_the_upload_read_one_list(web_app, monkeypatch):
    """Whatever `editor_terrains` says is what both routes go by."""
    from app.spawn_editor import routes

    monkeypatch.setattr(routes, "editor_terrains", lambda me: ["onlythis"])
    with TestClient(web_app) as client:
        _login(client)
        picker = [m["terrain"] for m in client.get("/api/map-editor/maps").json()]
        known = client.post("/api/map-editor/map-image?terrain=onlythis", content=PNG)
        dropped = client.post("/api/map-editor/map-image?terrain=chernarusplus", content=PNG)
    assert picker == ["onlythis"]
    assert known.status_code == 200 and dropped.status_code == 404


def test_only_an_admin_can_upload_or_remove_a_map_image(web_app):
    """No MODERATOR_WRITES entry: the permissions default makes both admin-only."""
    from app.maps import map_data

    web_app.state.ctx.users.add("mandy", "mandypass", "moderator")
    with TestClient(web_app) as client:
        client.post("/api/auth/login", json={"username": "mandy", "password": "mandypass"})
        assert client.post("/api/map-editor/map-image?terrain=enoch", content=PNG).status_code == 403
        assert client.delete("/api/map-editor/map-image?terrain=enoch").status_code == 403
    assert map_data.own_image("enoch") is None


def test_a_building_position_that_is_not_finite_does_not_fail_the_route(web_app, tmp_mission_dir):
    """NB-1: one `pos="1e999 …"` made /buildings answer 500 for the terrain."""
    (tmp_mission_dir / "mapgrouppos.xml").write_text(
        '<map>\n<group name="Land_Test" pos="1e999 50 5" />\n'
        '<group name="Land_Test" pos="300 50 400" />\n</map>\n',
        encoding="utf-8",
    )
    with TestClient(web_app) as client:
        _login(client)
        resp = client.get("/api/map-editor/buildings")
    assert resp.status_code == 200
    assert resp.json()["points"] == [300, 400]


def test_buildings_come_from_the_running_mission(web_app, tmp_mission_dir):
    """Invented positions; flat [x, z, x, z, …] in game metres."""
    (tmp_mission_dir / "mapgrouppos.xml").write_text(
        '<map>\n<group name="Land_Test" pos="1001.2 50 2002.6" />\n'
        '<group name="Land_Test" pos="300 50 400" />\n</map>\n',
        encoding="utf-8",
    )
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/buildings").json()
    assert body == {
        "terrain": "chernarusplus",
        "mission": "dayzOffline.chernarusplus",
        "count": 2,
        "points": [300, 400, 1000, 2005],
    }


def test_buildings_for_a_previewed_terrain_use_its_installed_mission(web_app, tmp_mission_dir):
    other = tmp_mission_dir.parent / "empty.deerisle"
    other.mkdir()
    (other / "mapgrouppos.xml").write_text('<map><group pos="10 1 20" /></map>', encoding="utf-8")
    with TestClient(web_app) as client:
        _login(client)
        deer = client.get("/api/map-editor/buildings?terrain=deerisle").json()
        none = client.get("/api/map-editor/buildings?terrain=namalsk").json()
    assert deer["mission"] == "empty.deerisle"
    assert deer["points"] == [10, 20]
    assert none == {"terrain": "namalsk", "mission": None, "count": 0, "points": []}


def test_map_info_says_whether_there_are_buildings_to_draw(web_app, tmp_mission_dir):
    """The Buildings toggle greys out before it is ticked when there are none."""
    other = tmp_mission_dir.parent / "empty.deerisle"
    other.mkdir()
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/map-info?terrain=deerisle").json()["has_buildings"] is False
        (other / "mapgrouppos.xml").write_text('<map><group pos="10 1 20" /></map>', encoding="utf-8")
        assert client.get("/api/map-editor/map-info?terrain=deerisle").json()["has_buildings"] is True
        assert client.get("/api/map-editor/map-info?terrain=namalsk").json()["has_buildings"] is False


def test_buildings_require_a_session(web_app):
    with TestClient(web_app) as client:
        assert client.get("/api/map-editor/buildings").status_code == 401


# ---------------------------------------------------------------------------
# The loot layer (plans/82). Invented files in Bohemia's shape.
# ---------------------------------------------------------------------------


def _loot_mission(mission):
    from app.maps import loot_map

    loot_map._cache.clear()
    from tests.test_loot_map import ECONOMYCORE, POS, PROTO, TYPES

    server = mission.parent.parent
    (server / "@FOX WEAPONS 2.0").mkdir(exist_ok=True)
    (mission / "custom").mkdir(exist_ok=True)
    (mission / "mapgroupproto.xml").write_text(PROTO)
    (mission / "mapgrouppos.xml").write_text(POS)
    (mission / "db" / "types.xml").write_text(TYPES)
    (mission / "cfgeconomycore.xml").write_text(ECONOMYCORE)
    (mission / "custom" / "fox-weapons-2.0-types_guns.xml").write_text(
        "<types>"
        '<type name="Fox_Rifle"><nominal>3</nominal><category name="weapons"/>'
        '<usage name="Military"/><value name="Tier4"/></type>'
        '<type name="Fox_Moon"><nominal>3</nominal><category name="weapons"/>'
        '<usage name="Moon"/></type>'
        "</types>"
    )
    (mission / "custom" / "user-mine.xml").write_text("<types/>")


def test_loot_catalog_lists_every_building_kind_that_holds_loot(web_app, tmp_mission_dir):
    """The Building view's list: most common first, empty containers left out."""
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/loot/catalog").json()
    kinds = body["buildings"]
    # Every loot building, sorted by x, with the index of its kind.
    assert body["building_points"] == [100, 200, 300, 400, 5000, 6000]
    assert body["building_point_kinds"] == [0, 0, 1]
    assert [(k["name"], k["buildings"]) for k in kinds] == [("Land_Shed", 2), ("Land_Barracks", 1)]
    shed, barracks = kinds
    assert shed["usages"] == ["Farm"] and shed["first"] == [100, 200]
    assert barracks["containers"] == ["lootFloor"]  # lootEmpty has no point
    assert barracks["items"] >= 1


def test_loot_catalog_lists_items_and_their_owners(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/loot/catalog").json()
    assert body["mission"] == "dayzOffline.chernarusplus"
    assert body["problem"] is None
    assert body["owners"] == [
        {"key": "vanilla", "label": "Vanilla (db/types.xml)", "count": 10},
        {"key": "@FOX WEAPONS 2.0", "label": "@FOX WEAPONS 2.0", "count": 2},
    ]
    items = {row[0]: row for row in body["items"]}
    assert items["Fox_Rifle"][1] == 1
    assert items["Hammer"][1] == 0
    assert [row[0] for row in body["items"]] == sorted(items, key=str.lower)
    assert body["categories"] == ["clothes", "food", "tools", "weapons"]
    assert body["usages"] == ["Farm", "Military", "Moon"]
    _, _, category, usages, nominal, never = items["Hammer"]
    assert body["categories"][category] == "tools"
    assert [body["usages"][u] for u in usages] == ["Farm"]
    assert (nominal, never) == (10, "")
    assert items["Zero"][5] == "nominal 0"
    assert items["Anything"][2:4] == [2, []]


def test_loot_for_an_item_gives_its_buildings_flat_and_sorted(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/loot?item=anything").json()
    assert body["selection"] == {"kind": "item", "key": "Anything", "label": "Anything"}
    assert body["points"] == [100, 200, 300, 400]
    assert body["count"] == 2
    assert body["groups"][0]["name"] == "Land_Shed"
    assert body["tiers_unchecked"] is False
    assert body["truncated"] is False


def test_loot_for_a_mod_covers_its_items(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/loot", params={"mod": "@fox weapons 2.0"}).json()
        vanilla = client.get("/api/map-editor/loot?mod=vanilla").json()
    assert body["selection"]["key"] == "@FOX WEAPONS 2.0"
    assert [i["name"] for i in body["items"]] == ["Fox_Moon", "Fox_Rifle"]
    assert body["items"][0]["reason"] == "no building has its usage"
    assert body["matched"] == 1
    assert body["points"] == [5000, 6000]
    assert body["tiers_unchecked"] is True
    assert vanilla["selection"]["label"] == "Vanilla (db/types.xml)"
    assert len(vanilla["items"]) == 10


def test_loot_needs_exactly_one_of_item_and_mod(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/loot").status_code == 400
        assert client.get("/api/map-editor/loot?item=Hammer&mod=vanilla").status_code == 400


def test_loot_for_an_unknown_item_is_404(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        resp = client.get("/api/map-editor/loot?item=No_Such_Thing")
        mod = client.get("/api/map-editor/loot", params={"mod": "../../etc"})
    assert resp.status_code == 404
    assert "No_Such_Thing" in resp.json()["detail"]
    assert mod.status_code == 404


def test_loot_with_an_overlong_name_is_refused(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/loot", params={"item": "x" * 201}).status_code == 422
        assert client.get("/api/map-editor/loot", params={"mod": "x" * 201}).status_code == 422


def test_loot_for_a_terrain_with_no_mission_is_an_answer(web_app, tmp_mission_dir):
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/loot/catalog?terrain=namalsk").json()
        item = client.get("/api/map-editor/loot?terrain=namalsk&item=Hammer")
    assert body == {"terrain": "namalsk", "mission": None, "problem": "no_mission", "owners": [],
                    "categories": [], "usages": [], "items": [], "buildings": [],
                        "building_points": [], "building_point_kinds": [], "unreadable": []}
    assert item.status_code == 404


def test_loot_at_names_the_nearest_building_and_what_it_holds(web_app, tmp_mission_dir):
    from app.spawn_editor.converters import game_to_latlng

    _loot_mission(tmp_mission_dir)
    near_lat, near_lng = game_to_latlng(110, 205, "chernarusplus")
    far_lat, far_lng = game_to_latlng(2000, 2000, "chernarusplus")
    with TestClient(web_app) as client:
        _login(client)
        body = client.get("/api/map-editor/loot/at", params={"lat": near_lat, "lng": near_lng}).json()
        none = client.get("/api/map-editor/loot/at", params={"lat": far_lat, "lng": far_lng}).json()
        bad = client.get("/api/map-editor/loot/at", params={"lat": 0, "lng": 0, "radius": 5000})
    building = body["building"]
    assert building["name"] == "Land_Shed"
    assert building["position"] == [100, 200]
    assert building["on_map"] == 2
    assert building["usages"] == ["Farm"]
    assert [c["name"] for c in building["containers"]] == ["lootFloor", "lootshelves"]
    assert [i["name"] for i in building["items"]] == ["Anything", "Hammer", "Jacket"]
    assert none["building"] is None
    assert bad.status_code == 422


def test_a_viewer_can_read_the_loot_layer(web_app, tmp_mission_dir):
    _loot_mission(tmp_mission_dir)
    web_app.state.ctx.users.add("looker", "lookpass", "viewer")
    with TestClient(web_app) as client:
        assert client.get("/api/map-editor/loot/catalog").status_code == 401
        assert client.post(
            "/api/auth/login", json={"username": "looker", "password": "lookpass"}
        ).status_code == 200
        assert client.get("/api/map-editor/loot/catalog").status_code == 200
        assert client.get("/api/map-editor/loot?item=Hammer").status_code == 200
        assert client.get("/api/map-editor/loot/at?lat=0&lng=0").status_code == 200


def test_spa_route_serves_the_spa(web_app):
    """/map-editor is a React route: it must fall through to the SPA index.

    Kept from the deleted mount's tests — the /map-editor mount used to be
    registered before this fallback, and getting that order wrong made the
    catch-all swallow one of them.
    """
    with TestClient(web_app) as client:
        _login(client)
        resp = client.get("/map-editor")
        # 200 with the SPA index when built assets exist; 404 in a bare checkout
        assert resp.status_code in (200, 404)


def test_reload_rebuilds_state_and_restarts_the_watcher(web_app):
    """POST /reload is what a map switch calls; it must not leave the old
    watcher running against the old state."""
    state = web_app.state.ctx
    with TestClient(web_app) as client:
        _login(client)

        with client.websocket_connect("/ws") as ws:
            _drain_hello(ws)
            ws.send_json({"action": "subscribe", "channel": "mapeditor"})
            assert _wait_for(
                lambda: state.map_editor is not None
                and state.map_editor.watcher_task is not None
            )
            first_me = state.map_editor
            first_watcher = first_me.watcher_task

            assert client.post("/api/map-editor/reload").json()["loaded"] is True

            assert state.map_editor is not first_me
            assert state.map_editor.watcher_task is not first_watcher
            assert first_watcher.done() or first_watcher.cancelled()


def test_editor_state_is_lazy_and_shared(web_app):
    state = web_app.state.ctx
    with TestClient(web_app) as client:
        _login(client)
        assert state.map_editor is None
        assert client.get("/api/map-editor/status").json()["loaded"] is False

        client.get("/api/map-editor/spawns")
        me = state.map_editor
        assert me is not None

        body = client.get("/api/map-editor/status").json()
        assert body["loaded"] is True
        assert body["terrain"] == "chernarusplus"
        assert body["traderplus"] is False

        # sticky: a second call reuses the same state object
        client.get("/api/map-editor/map-info")
        assert state.map_editor is me


def test_a_mission_switched_outside_the_panel_rebuilds_the_editor(
    web_app, sample_config, tmp_mission_dir
):
    """`zsm maps switch` from a shell never calls reload_map_editor — the web
    process only learns of it through serverDZ.cfg. Measured in 2026 Q3 (plan
    46 A): the editor kept drawing Sakhal after a CLI switch to Chernarus."""
    state = web_app.state.ctx
    # A second mission, empty but real, for the cfg to switch to.
    enoch = tmp_mission_dir.parent / "dayzOffline.enoch"
    (enoch / "db").mkdir(parents=True)
    for f in ("db/events.xml", "cfgeventspawns.xml"):
        (enoch / f).write_text((tmp_mission_dir / f).read_text(encoding="utf-8"), encoding="utf-8")

    with TestClient(web_app) as client:
        _login(client)
        client.get("/api/map-editor/map-info")
        first = state.map_editor
        assert first.terrain == "chernarusplus"

        # Same mission: sticky, as before.
        client.get("/api/map-editor/map-info")
        assert state.map_editor is first

        cfg = sample_config.server_cfg_path
        cfg.write_text(
            cfg.read_text(encoding="utf-8").replace("dayzOffline.chernarusplus", "dayzOffline.enoch"),
            encoding="utf-8",
        )
        client.get("/api/map-editor/map-info")
        assert state.map_editor is not first
        assert state.map_editor.terrain == "enoch"
        assert state.map_editor.mission == "dayzOffline.enoch"


def test_a_mod_layer_that_appears_later_rebuilds_the_editor(web_app, tmp_mission_dir):
    """A mod install, an Expansion template or a first boot writes the mod's
    files after the editor was built. The layer toggles gate on the state
    existing, so the layer stayed off until `zsm web` restarted."""
    state = web_app.state.ctx
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/map-info").json()["expansion"] is False
        first = state.map_editor

        # Nothing changed: sticky, as before.
        client.get("/api/map-editor/map-info")
        assert state.map_editor is first

        path = tmp_mission_dir.joinpath("expansion", "settings", "SafeZoneSettings.json")
        path.parent.mkdir(parents=True)
        path.write_text('{"CircleZones": []}', encoding="utf-8")

        assert client.get("/api/map-editor/map-info").json()["expansion"] is True
        assert state.map_editor is not first
        assert state.map_editor.exp_safezone_state is not None


def test_help_is_served_and_guarded(web_app):
    """The React help dialog reads this; it lives on the shared editor router
    so the standalone editor serves the same JSON at /api/help."""
    with TestClient(web_app) as client:
        assert client.get("/api/map-editor/help").status_code == 401
        _login(client)
        data = client.get("/api/map-editor/help").json()
        assert data["title"]
        assert data["sections"]


# ---------------------------------------------------------------------------
# WebSocket channel
# ---------------------------------------------------------------------------

def _drain_hello(ws) -> None:
    msg = ws.receive_json()
    assert msg["type"] == "hello"


def test_mutation_pings_only_channel_subscribers(web_app):
    with TestClient(web_app) as client:
        _login(client)
        client.get("/api/map-editor/spawns")  # force init

        with client.websocket_connect("/ws") as subbed, client.websocket_connect("/ws") as other:
            _drain_hello(subbed)
            _drain_hello(other)
            subbed.send_json({"action": "subscribe", "channel": "mapeditor"})

            resp = client.post("/api/map-editor/events", json={"name": "TestEventPing"})
            assert resp.status_code == 200

            types = set()
            for _ in range(3):
                types.add(subbed.receive_json()["type"])
            assert "mapeditor_events_changed" in types
            assert "mapeditor_spawns_changed" in types

            # The unsubscribed socket sees nothing on the mapeditor channel.
            other.send_json({"action": "subscribe", "channel": "status"})
            other.send_json({"action": "unsubscribe", "channel": "status"})
            client.get("/api/map-editor/spawns")


def test_full_reload_is_namespaced_without_payload():
    """`full_reload` carries the whole cfgeventspawns dump for the old client;
    the web channel gets a bare ping so React has one refetch path."""
    from app.spawn_editor.state import to_web_message

    assert to_web_message({"type": "full_reload", "data": {"events": []}}) == {
        "type": "mapeditor_spawns_changed"
    }
    assert to_web_message({"type": "garage_changed"}) == {
        "type": "mapeditor_garage_changed"
    }
    assert to_web_message({"type": "something_else"}) is None


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    """Poll from the main thread; the app's loop runs in the portal thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_watcher_runs_only_while_subscribed(web_app):
    state = web_app.state.ctx
    with TestClient(web_app) as client:
        _login(client)

        with client.websocket_connect("/ws") as ws:
            _drain_hello(ws)
            # the first subscriber's hook inits state, reloads, starts the watcher
            ws.send_json({"action": "subscribe", "channel": "mapeditor"})

            assert _wait_for(
                lambda: state.map_editor is not None
                and state.map_editor.watcher_task is not None
            ), "watcher did not start on first subscriber"
            me = state.map_editor
            watcher = me.watcher_task
            assert not watcher.done()

        # last unsubscriber (disconnect) stops the watcher but keeps the state
        assert _wait_for(lambda: state.map_editor.watcher_task is None), (
            "watcher did not stop on last unsubscriber"
        )
        assert state.map_editor is me, "state must be sticky across subscribers"
        assert watcher.cancelled() or watcher.done()


# ---------------------------------------------------------------------------
# Regression: clicking the map to add a player spawn
# ---------------------------------------------------------------------------

def test_add_player_spawn_accepts_latlng(web_app):
    """The old JS frontend converted lat/lng -> game coords itself and got the
    Z axis mirrored about the map centre (gameZ = map_size - correct). The
    endpoint takes lat/lng directly so no client repeats that."""
    from app.spawn_editor.converters import game_to_latlng

    with TestClient(web_app) as client:
        _login(client)

        # A point well north of centre, where a mirrored Z is unmistakable.
        game_x, game_z = 4000.0, 12000.0
        lat, lng = game_to_latlng(game_x, game_z, "chernarusplus")

        resp = client.post(
            "/api/map-editor/player-spawns/fresh/groups/0/positions",
            json={"lat": lat, "lng": lng},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["game_x"] == pytest.approx(game_x, abs=0.01)
        assert body["game_z"] == pytest.approx(game_z, abs=0.01)
        # not the mirrored value the old client would have produced
        assert body["game_z"] != pytest.approx(15360 - game_z, abs=1.0)


def test_add_player_spawn_still_accepts_game_coords(web_app):
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post(
            "/api/map-editor/player-spawns/fresh/groups/0/positions",
            json={"x": 1234.0, "z": 5678.0},
        )
        assert resp.status_code == 200
        assert resp.json()["game_x"] == pytest.approx(1234.0, abs=0.01)
        assert resp.json()["game_z"] == pytest.approx(5678.0, abs=0.01)


def test_add_player_spawn_rejects_neither(web_app):
    with TestClient(web_app) as client:
        _login(client)
        resp = client.post(
            "/api/map-editor/player-spawns/fresh/groups/0/positions", json={"foo": 1}
        )
        assert resp.status_code == 400


# The <fresh> params of Bohemia's 1.29 Chernarus cfgplayerspawnpoints.xml,
# read at the pinned commit: no <min_dist_trigger>/<max_dist_trigger>, which
# the Players tab's save used to add as 0 because the parser filled absent
# fields with "0". Lines 1-29 (WestCherno's first position), WestCherno's
# closing tag (33), the end of <fresh> (99-100) and the last line.
def chernarus_spawn_params() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgplayerspawnpoints.xml"))
    return "".join(lines[:29] + lines[32:33] + lines[98:100] + lines[-1:])


@pytest.fixture()
def chernarus_spawns(web_app, tmp_mission_dir):
    path = tmp_mission_dir / "cfgplayerspawnpoints.xml"
    path.write_bytes(chernarus_spawn_params().encode())
    return path


def _put_params(client, block: str, body):
    return client.put(f"/api/map-editor/player-spawns/fresh/params/{block}", json=body)


def test_an_unchanged_params_save_leaves_the_file_as_it_was(web_app, chernarus_spawns):
    with TestClient(web_app) as client:
        _login(client)
        fresh = client.get("/api/map-editor/player-spawns").json()[0]
        # Absent in the file: absent in the payload, not a made-up 0.
        assert fresh["spawn_params"]["min_dist_trigger"] is None
        assert fresh["spawn_params"]["min_dist_infected"] == "30"
        for block in ("spawn", "generator", "group"):
            sent = fresh[f"{block}_params"]
            # As the old form sent it (every field, absent ones empty) and as loaded.
            assert _put_params(client, block, {k: v or "" for k, v in sent.items()}).status_code == 200
            assert _put_params(client, block, sent).status_code == 200
    assert chernarus_spawns.read_bytes() == chernarus_spawn_params().encode()


def test_a_params_save_writes_only_the_value_that_changed(web_app, chernarus_spawns):
    with TestClient(web_app) as client:
        _login(client)
        resp = _put_params(client, "spawn", {"min_dist_infected": "40", "max_dist_infected": "70.0"})
        assert resp.status_code == 200
        assert resp.json()["min_dist_trigger"] is None
    assert chernarus_spawns.read_text() == chernarus_spawn_params().replace(
        "<min_dist_infected>30<", "<min_dist_infected>40<"
    )


@pytest.mark.parametrize("block, body", [
    ("spawn", {"min_dist_infected": "abc"}),
    ("spawn", {"min_dist_infected": ""}),
    ("spawn", {"min_dist_player": "1e999"}),
    ("generator", {"grid_density": "4.5"}),
    ("group", {"enablegroups": "maybe"}),
    ("group", {"lifetime": "60.5"}),
])
def test_a_params_value_dayz_cannot_read_is_refused(web_app, chernarus_spawns, block, body):
    with TestClient(web_app) as client:
        _login(client)
        resp = _put_params(client, block, body)
        assert resp.status_code == 400, resp.text
        assert "fresh" in resp.json()["detail"]
        # Nothing went into the editor's copy either.
        fresh = client.get("/api/map-editor/player-spawns").json()[0]
        assert fresh["generator_params"]["grid_density"] == "4"
    assert chernarus_spawns.read_bytes() == chernarus_spawn_params().encode()


@pytest.mark.parametrize("body, ok", [
    ({"grid_width": "4096", "grid_height": "1"}, False),  # density 4 > the 1 m side
    ({"grid_width": "4096"}, True),
    ({"grid_width": "4097"}, False),
    ({"grid_height": "0"}, False),
    ({"grid_density": "200"}, True),
    ({"grid_density": "201"}, False),
    ({"grid_density": "0"}, True),  # the position alone
    ({"grid_density": "-1"}, False),
])
def test_the_grid_follows_bohemias_limits(web_app, chernarus_spawns, body, ok):
    """Sakhal's stock file: width and height are 1 to 4096 metres, and the
    density is at least 1 and no larger than the smaller side, 0 for the
    centre point alone."""
    with TestClient(web_app) as client:
        _login(client)
        resp = _put_params(client, "generator", body)
        assert resp.status_code == (200 if ok else 400), resp.text
    if not ok:
        assert chernarus_spawns.read_bytes() == chernarus_spawn_params().encode()


def test_a_player_spawn_sent_back_as_shown_keeps_the_files_text(web_app, tmp_mission_dir):
    path = tmp_mission_dir / "cfgplayerspawnpoints.xml"
    text = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<playerspawnpoints>\n"
        "  <fresh>\n"
        "    <generator_posbubbles>\n"
        '      <group name="West">\n'
        '        <pos x="1234.5678" z="2593.47" />\n'
        "      </group>\n"
        "    </generator_posbubbles>\n"
        "  </fresh>\n"
        "</playerspawnpoints>\n"
    )
    path.write_text(text, encoding="utf-8")
    url = "/api/map-editor/player-spawns/fresh/groups/0/positions/0/game"
    with TestClient(web_app) as client:
        _login(client)
        # The popup sends back the 2-decimal values it showed.
        assert client.put(url, json={"x": 1234.57, "z": 2593.47}).status_code == 200
        assert path.read_text(encoding="utf-8") == text
        # A real edit changes only the edited coordinate.
        assert client.put(url, json={"x": 1300.25, "z": 2593.47}).status_code == 200
    assert path.read_text(encoding="utf-8") == text.replace('x="1234.5678"', 'x="1300.250000"')


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

def test_reload_rebuilds_state(web_app):
    state = web_app.state.ctx
    with TestClient(web_app) as client:
        _login(client)
        client.get("/api/map-editor/spawns")
        first = state.map_editor

        assert client.post("/api/map-editor/reload").status_code == 200
        assert state.map_editor is not None
        assert state.map_editor is not first


def test_a_write_with_no_page_open_keeps_a_change_made_on_disk(web_app, tmp_mission_dir):
    """The watcher runs only while a page subscribes, so a script's write saved
    the copy loaded earlier: an event added on disk since then was lost, and
    one deleted there came back (browser test 2026-11-17, F9)."""
    spawns = tmp_mission_dir / "cfgeventspawns.xml"
    spawns.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<eventposdef>\n'
        '\t<event name="Gone">\n\t\t<pos x="100.000000" z="100.000000" a="0"/>\n\t</event>\n'
        '\t<event name="Kept">\n\t\t<pos x="200.000000" z="200.000000" a="0"/>\n\t</event>\n'
        "</eventposdef>\n",
        encoding="utf-8",
    )
    with TestClient(web_app) as client:
        _login(client)
        assert client.get("/api/map-editor/spawns").status_code == 200
        spawns.write_text(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<eventposdef>\n'
            '\t<event name="Kept">\n\t\t<pos x="200.000000" z="200.000000" a="0"/>\n\t</event>\n'
            '\t<event name="AddedOnDisk">\n\t\t<pos x="300.000000" z="300.000000" a="0"/>\n\t</event>\n'
            "</eventposdef>\n",
            encoding="utf-8",
        )
        resp = client.post("/api/map-editor/spawns/Kept", json={"lat": 10.0, "lng": 10.0})
        assert resp.status_code == 200, resp.text

    text = spawns.read_text(encoding="utf-8")
    assert "AddedOnDisk" in text
    assert "Gone" not in text
    assert 'x="8600' in text
