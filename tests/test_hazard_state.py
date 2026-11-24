"""The Hazard zones map layers (`app/spawn_editor/hazard_state.py`, plans/48 C).

Three sources on one layer family: the mission's static contaminated areas
(cfgEffectArea.json, vanilla) and, with each mod installed, RadZone's and
ToxicZone's zones and crates — ToxicZone's moving zones too. The contaminated fixture is shaped like Bohemia's stock file — integer
positions and radii, particle names, a `PlayerData` block — because the thing
most worth pinning is that a map drag writes the centre and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ops import zone_mods
from app.spawn_editor.converters import game_to_latlng
from tests.test_zone_mods import MOD, _area, _crates, _radiation, _write, _zone
from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, mods_lines, write_test_mods

PASSWORD = "testpass123"
TERRAIN = "chernarusplus"


def _area_entry(name: str, x, z, radius) -> dict:
    return {
        "AreaName": name,
        "Type": "ContaminatedArea_Static",
        "TriggerType": "ContaminatedTrigger",
        "Data": {
            "Pos": [x, 0, z],
            "Radius": radius,
            "PosHeight": 20,
            "NegHeight": 3,
            "ParticleName": "graphics/particles/contaminated_area_gas_bigass",
        },
        "PlayerData": {"PPERequesterType": "PPERequester_ContaminatedAreaTint"},
    }


EFFECT_AREAS = {
    "Areas": [_area_entry("Ship-Bow", 13917, 11178, 75), _area_entry("Pavlovo", 2100.5, 3300.25, 150.0)],
    "SafePositions": [[1000, 1000], [2000, 2000]],
}


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


@pytest.fixture()
def effect_file(tmp_mission_dir: Path) -> Path:
    # Stock capitalisation; the reader matches it case-insensitively.
    path = tmp_mission_dir / "cfgEffectArea.json"
    path.write_text(json.dumps(EFFECT_AREAS, indent=4), encoding="utf-8")
    return path


def _install_radzone(cfg, *zones, crates=None) -> None:
    write_test_mods(cfg, mods_lines(f"@CF\n{MOD}\n"))
    (cfg.server_dir_path / MOD).mkdir(exist_ok=True)
    _write(cfg, zone_mods.RADIATION, _radiation(*zones))
    _write(cfg, zone_mods.CRATES, crates or _crates())


def _radzone_doc(cfg) -> dict:
    return json.loads((zone_mods.config_dir(cfg) / zone_mods.RADIATION).read_text())


SQUARE = _zone("Yard", x=0.0, y=0.0, square=1, corners=(4000.0, 5000.0, 4200.0, 5100.0))


@pytest.fixture()
def client(sample_config, tmp_mission_dir, effect_file):
    _install_radzone(sample_config, _zone("North"), SQUARE, _zone("Unset", square=1))
    return _login(sample_config)


def _hazards(client) -> dict:
    return client.get("/api/map-editor/hazards").json()


# ── reading ───────────────────────────────────────────────────────


def test_contaminated_areas_carry_their_centre_and_radius(client):
    areas = _hazards(client)["contaminated"]
    assert [a["name"] for a in areas] == ["Ship-Bow", "Pavlovo"]
    bow = areas[0]
    assert (bow["game_x"], bow["game_z"], bow["radius"]) == (13917, 11178, 75)
    expected = _latlng(13917, 11178)
    assert bow["lat"] == pytest.approx(expected["lat"], abs=1e-5)
    assert bow["lng"] == pytest.approx(expected["lng"], abs=1e-5)


def test_radzone_zones_come_in_both_shapes(client):
    radzone = _hazards(client)["radzone"]
    circle, square, unset = radzone["zones"]
    assert circle["shape"] == "circle" and circle["radius"] == 300.0
    assert "corners_set" not in circle
    assert square["shape"] == "square" and square["corners_set"] is True
    centre = _latlng(4100.0, 5050.0)
    assert square["centre"]["lat"] == pytest.approx(centre["lat"], abs=1e-5)
    assert square["sw"]["lat"] == pytest.approx(_latlng(4000.0, 5000.0)["lat"], abs=1e-5)
    assert unset["corners_set"] is False


def test_crates_are_listed_with_their_area_and_loot(client):
    crates = _hazards(client)["radzone"]["crates"]
    assert crates == [{"area": "North", "index": 0, "loot": "Army", **{
        k: pytest.approx(v, abs=1e-5) for k, v in _latlng(5000.0, 6000.0).items()
    }}]
    assert _hazards(client)["radzone"]["crates_active"] is True


def test_map_info_says_whether_radzone_is_there(client, sample_config):
    assert client.get("/api/map-editor/map-info").json()["radzone"] is True


def test_an_uninstalled_radzone_is_not_drawn_from_its_leftover_folder(
    sample_config, tmp_mission_dir, effect_file
):
    """Uninstall keeps profiles/RadZone/ (live data); the zones are not active."""
    _write(sample_config, zone_mods.RADIATION, _radiation())
    client = _login(sample_config)
    body = _hazards(client)
    assert body["radzone"] is None and len(body["contaminated"]) == 2
    assert client.get("/api/map-editor/map-info").json()["radzone"] is False
    assert client.put("/api/map-editor/hazards/radzone/0", json={"radius": 5}).status_code == 404


def test_a_mission_with_neither_file_answers_empty(sample_config, tmp_mission_dir):
    client = _login(sample_config)
    body = _hazards(client)
    assert body["contaminated"] == [] and body["radzone"] is None
    resp = client.put("/api/map-editor/hazards/contaminated/0", json={"radius": 5})
    assert resp.status_code == 404


def test_radzone_installed_after_the_editor_opened_shows_on_a_reload(
    sample_config, tmp_mission_dir, effect_file
):
    """The editor state lives as long as the process, and RadZone writes its
    files on its first boot — long after the map may have been opened."""
    client = _login(sample_config)
    assert _hazards(client)["radzone"] is None
    _install_radzone(sample_config, _zone("Late"))
    assert client.post("/api/map-editor/reload").status_code == 200
    assert [z["name"] for z in _hazards(client)["radzone"]["zones"]] == ["Late"]


def test_the_effect_area_file_is_found_under_db_too(sample_config, tmp_mission_dir):
    (tmp_mission_dir / "db" / "CFGEFFECTAREA.JSON").write_text(json.dumps(EFFECT_AREAS))
    client = _login(sample_config)
    assert len(_hazards(client)["contaminated"]) == 2


# ── contaminated areas ────────────────────────────────────────────


def test_moving_an_area_writes_its_centre_and_nothing_else(client, effect_file):
    resp = client.put("/api/map-editor/hazards/contaminated/0", json=_latlng(13000.4, 11000.6))
    assert resp.status_code == 200
    doc = json.loads(effect_file.read_text())
    bow = doc["Areas"][0]
    # Whole numbers in the file stay whole; the height (0 = ground) is kept.
    assert bow["Data"]["Pos"] == [13000, 0, 11001]
    assert all(isinstance(v, int) for v in bow["Data"]["Pos"])
    expected = _area_entry("Ship-Bow", 13000, 11001, 75)
    assert bow == expected
    assert doc["Areas"][1] == EFFECT_AREAS["Areas"][1]
    assert doc["SafePositions"] == EFFECT_AREAS["SafePositions"]


def test_resizing_an_area_keeps_the_radius_type(client, effect_file):
    client.put("/api/map-editor/hazards/contaminated/0", json={"radius": 120.7})
    client.put("/api/map-editor/hazards/contaminated/1", json={"radius": 99.456})
    areas = json.loads(effect_file.read_text())["Areas"]
    assert areas[0]["Data"]["Radius"] == 121 and isinstance(areas[0]["Data"]["Radius"], int)
    assert areas[1]["Data"]["Radius"] == 99.46


def chernarus_start() -> str:
    """The start of Chernarus' cfgEffectArea.json, read at the pinned 1.29
    commit: tabs, trailing spaces, `"Pos": [ x, y, z ]`. Its first area,
    without the comma that leads on to the next, then the file's own last two
    lines (no final newline)."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgEffectArea.json"))
    *area, close = ce_block(lines, '"AreaName": "Ship-Bow"')
    return "".join(lines[:2] + area + [close.replace("},", "}", 1)] + lines[-2:])


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_save_changes_only_its_own_line_of_bohemia_s_file(client, tmp_mission_dir, newline):
    """The first map edit used to reformat the whole file (450 changed lines on
    Chernarus); now the file is patched, line endings included."""
    text = chernarus_start().replace("\n", newline)
    path = tmp_mission_dir / "cfgEffectArea.json"
    path.write_bytes(text.encode("utf-8"))
    assert client.put("/api/map-editor/hazards/contaminated/0", json={"radius": 90}).status_code == 200
    want = text.replace('"Radius": 75,', '"Radius": 90,')
    assert path.read_bytes() == want.encode("utf-8")


def test_bad_requests_write_nothing(client, effect_file):
    before = effect_file.read_text()
    assert client.put("/api/map-editor/hazards/contaminated/9", json={"radius": 5}).status_code == 404
    assert client.put("/api/map-editor/hazards/contaminated/-1", json={"radius": 5}).status_code == 404
    assert client.put(
        "/api/map-editor/hazards/contaminated/0", json={"lat": "north", "lng": 1.0}
    ).status_code == 400
    assert client.put("/api/map-editor/hazards/contaminated/0", json={"radius": None}).status_code == 400
    assert client.put("/api/map-editor/hazards/contaminated/0", json=[1, 2]).status_code == 400
    assert effect_file.read_text() == before


# ── RadZone ───────────────────────────────────────────────────────


def test_moving_and_resizing_a_circle_zone(client, sample_config):
    assert client.put("/api/map-editor/hazards/radzone/0", json=_latlng(5500.0, 6500.0)).status_code == 200
    assert client.put("/api/map-editor/hazards/radzone/0", json={"radius": 450}).status_code == 200
    zone = _radzone_doc(sample_config)["RadiationAreaLocation"][0]
    assert zone["X"] == pytest.approx(5500.0, abs=0.02)
    assert zone["Y"] == pytest.approx(6500.0, abs=0.02)
    assert zone["Radius"] == 450.0 and isinstance(zone["Radius"], float)
    assert zone["MsgEnterZone"] == "in" and zone["NbSickGivenForRadiation"] == 50


def test_a_square_zone_moves_whole_and_keeps_its_size(client, sample_config):
    resp = client.put("/api/map-editor/hazards/radzone/1", json=_latlng(6100.0, 7050.0))
    assert resp.status_code == 200
    zone = _radzone_doc(sample_config)["RadiationAreaLocation"][1]
    assert zone["X1"] == pytest.approx(6000.0, abs=0.02)
    assert zone["X2"] == pytest.approx(6200.0, abs=0.02)
    assert zone["Y1"] == pytest.approx(7000.0, abs=0.02)
    assert zone["Y2"] == pytest.approx(7100.0, abs=0.02)
    # A circle's centre is not what places a square zone; it stays put.
    assert (zone["X"], zone["Y"]) == (0.0, 0.0)


def test_a_square_zone_ignores_a_radius(client, sample_config):
    client.put("/api/map-editor/hazards/radzone/1", json={"radius": 999})
    assert _radzone_doc(sample_config)["RadiationAreaLocation"][1] == SQUARE


def test_a_square_zone_with_unset_corners_refuses_to_move(client, sample_config):
    before = _radzone_doc(sample_config)
    resp = client.put("/api/map-editor/hazards/radzone/2", json=_latlng(100.0, 100.0))
    assert resp.status_code == 400 and "unset corners" in resp.json()["detail"]
    assert _radzone_doc(sample_config) == before


def test_a_radzone_write_keeps_the_settings_around_the_zones(client, sample_config):
    client.put("/api/map-editor/hazards/radzone/0", json={"radius": 310})
    doc = _radzone_doc(sample_config)
    assert doc["CriticalProtection"] == 70 and isinstance(doc["CriticalProtection"], int)
    assert doc["IsRadiationZoneActive"] == 1
    assert len(doc["RadiationAreaLocation"]) == 3


def test_a_crate_system_that_is_off_is_reported(sample_config, tmp_mission_dir):
    _install_radzone(sample_config, _zone(), crates=_crates(_area("North"), active=0))
    client = _login(sample_config)
    assert _hazards(client)["radzone"]["crates_active"] is False


# ── ToxicZone ─────────────────────────────────────────────────────


def _install_toxiczone(cfg, static=None, moving=None) -> None:
    from app.ops import toxic_zone
    from tests import test_toxic_zone as tzt

    write_test_mods(cfg, mods_lines(f"@CF\n{tzt.MOD}\n"))
    (cfg.server_dir_path / tzt.MOD).mkdir(exist_ok=True)
    tzt._write(cfg, toxic_zone.STATIC, static or tzt._static())
    tzt._write(cfg, toxic_zone.MOVING, moving or tzt._moving())


def _toxic_doc(cfg, name) -> dict:
    from app.ops import toxic_zone

    return json.loads((toxic_zone.config_dir(cfg) / name).read_text())


@pytest.fixture()
def tz_client(sample_config, tmp_mission_dir):
    _install_toxiczone(sample_config)
    return _login(sample_config)


def test_toxiczone_zones_moving_zones_and_crates(tz_client):
    body = _hazards(tz_client)
    assert body["radzone"] is None
    tz = body["toxiczone"]
    assert [z["name"] for z in tz["zones"]] == ["Plant"]
    assert tz["zones"][0]["radius"] == 300.0
    assert [(c["area"], c["loot"]) for c in tz["crates"]] == [("Plant", "Kit")]
    (line,) = tz["moving"]
    assert line["name"] == "Line" and line["speed"] == 20
    assert line["start"]["lat"] == pytest.approx(_latlng(1000.0, 2000.0)["lat"], abs=1e-5)
    assert line["end"]["lng"] == pytest.approx(_latlng(3000.0, 3000.0)["lng"], abs=1e-5)
    assert (line["stale"], line["backwards"], line["north_south"]) == (False, False, False)
    assert tz_client.get("/api/map-editor/map-info").json()["toxiczone"] is True


def test_the_mods_stale_default_is_marked_on_the_map(sample_config, tmp_mission_dir):
    from tests import test_toxic_zone as tzt

    stale = tzt._moving_zone("ZelenToNovy", start=(2000.0, 5000.0), end=(7442.0, 8330.0),
                             YtoOrigin=5000.0, Norme=6379)
    _install_toxiczone(sample_config, moving=tzt._moving(stale))
    assert _hazards(_login(sample_config))["toxiczone"]["moving"][0]["stale"] is True


def test_moving_a_static_toxic_zone_keeps_its_particles_and_crates(tz_client, sample_config):
    from app.ops import toxic_zone

    before = _toxic_doc(sample_config, toxic_zone.STATIC)["StaticToxicAreaLocation"][0]
    assert tz_client.put("/api/map-editor/hazards/toxiczone/0", json=_latlng(5500.0, 6500.0)).status_code == 200
    zone = _toxic_doc(sample_config, toxic_zone.STATIC)["StaticToxicAreaLocation"][0]
    assert zone["X"] == pytest.approx(5500.0, abs=0.02)
    assert zone["Y"] == pytest.approx(6500.0, abs=0.02)
    assert zone["ParticlesPosition"] == before["ParticlesPosition"]
    assert zone["ToxicCratePosition"] == before["ToxicCratePosition"]


def test_a_drag_rewrites_only_the_zone_it_moved(sample_config, tmp_mission_dir):
    """F75: one drag of a ToxicZone zone rewrote 38 numbers nobody touched,
    two of them into ``e-06`` form, because ToxicZone writes 17 significant
    digits and Python's shortest repr differs."""
    from app.ops import toxic_zone
    from tests import test_toxic_zone as tzt

    moved = tzt._static_zone("Tisy", x=1647.6500244140625, y=13000.0,
                             crates=((1647.6500244140625, -3.000000106112566e-06, 13000.0),))
    kept = tzt._static_zone("Plant", x=5000.0, y=6000.0,
                            crates=((5000.0, -3.000000106112566e-06, 6000.0),))
    text = json.dumps(tzt._static(moved, kept), indent=4)
    text = text.replace("1647.6500244140625", "1647.6500244140626")
    text = text.replace("-3.000000106112566e-06", "-0.000003000000106112566")
    _install_toxiczone(sample_config)
    path = toxic_zone.config_dir(sample_config) / toxic_zone.STATIC
    path.write_text(text, encoding="utf-8")
    client = _login(sample_config)

    resp = client.put("/api/map-editor/hazards/toxiczone/0", json=_latlng(1700.0, 13050.0))

    assert resp.status_code == 200, resp.text
    before, after = text.splitlines(), path.read_text(encoding="utf-8").splitlines()
    assert len(after) == len(before)
    changed = [(a.strip(), b.strip()) for a, b in zip(before, after) if a != b]
    assert [a for a, _ in changed] == ['"X": 1647.6500244140626,', '"Y": 13000.0,']
    assert "e-06" not in "\n".join(after)


def test_dragging_an_end_recomputes_the_path_in_the_same_write(tz_client, sample_config):
    from app.ops import toxic_zone

    body = {"point": "end", **_latlng(500.0, 3000.0)}  # now west of the start (1000)
    assert tz_client.put("/api/map-editor/hazards/toxiczone-moving/0", json=body).status_code == 200
    zone = _toxic_doc(sample_config, toxic_zone.MOVING)["MovingToxicAreaLocation"][0]
    assert zone["EndX"] == pytest.approx(500.0, abs=0.02)
    assert zone["EndY"] == pytest.approx(3000.0, abs=0.02)
    want = toxic_zone.derived(zone["StartX"], zone["StartY"], zone["EndX"], zone["EndY"])
    assert zone["Coeff"] == pytest.approx(want.coeff)
    assert zone["YtoOrigin"] == pytest.approx(want.y_to_origin)
    assert zone["Norme"] == want.norme
    assert zone["Speed"] == -20  # it now runs west
    assert zone["MsgEnterZone"] == "in"


def test_a_north_south_path_is_refused_without_touching_memory(sample_config, tmp_mission_dir):
    """Called on the state directly: through the API the lat/lng round-trip
    never lands exactly on the start's X, which is the only case refused."""
    import asyncio

    from app.spawn_editor.hazard_state import HazardState
    from app.ops import toxic_zone

    _install_toxiczone(sample_config)

    class _Quiet:
        async def broadcast(self, msg):
            pass

    state = HazardState(sample_config, tmp_mission_dir, TERRAIN, _Quiet())
    state.load()
    state._target = lambda data: (1000.0, 2500.0)  # the start's X exactly
    with pytest.raises(ValueError, match="StartX and EndX are the same"):
        asyncio.run(state.update_toxic_moving(0, {"point": "end", "lat": 0, "lng": 0}))
    assert state._toxic_moving_zones()[0]["EndX"] == 3000.0
    with pytest.raises(ValueError, match="point must be"):
        asyncio.run(state.update_toxic_moving(0, {"point": "middle", "lat": 0, "lng": 0}))
    assert _toxic_doc(sample_config, toxic_zone.MOVING)["MovingToxicAreaLocation"][0]["EndX"] == 3000.0


def test_resizing_a_moving_zone_writes_only_its_radius(tz_client, sample_config):
    from app.ops import toxic_zone

    before = _toxic_doc(sample_config, toxic_zone.MOVING)["MovingToxicAreaLocation"][0]
    assert tz_client.put("/api/map-editor/hazards/toxiczone-moving/0", json={"radius": 220}).status_code == 200
    zone = _toxic_doc(sample_config, toxic_zone.MOVING)["MovingToxicAreaLocation"][0]
    assert zone == {**before, "Radius": 220.0}


def test_an_uninstalled_toxiczone_is_not_drawn(sample_config, tmp_mission_dir):
    from app.ops import toxic_zone
    from tests import test_toxic_zone as tzt

    tzt._write(sample_config, toxic_zone.STATIC, tzt._static())
    client = _login(sample_config)
    assert _hazards(client)["toxiczone"] is None
    assert client.put("/api/map-editor/hazards/toxiczone/0", json={"radius": 5}).status_code == 404



# ── plans/51 B: bad bodies are refused before anything moves ─────────────


def test_a_negative_radius_is_refused_not_clamped(client, effect_file):
    """N22: `radius: -50` used to become 1 m, which removes the area."""
    before = effect_file.read_bytes()
    for radius in (-50, 0):
        resp = client.put("/api/map-editor/hazards/contaminated/0", json={"radius": radius})
        assert resp.status_code == 400
        assert "greater than 0" in resp.json()["detail"]
    assert effect_file.read_bytes() == before


def test_a_bad_radius_does_not_half_apply_the_move(client, effect_file):
    """The state class moved the area in memory, then raised on the radius;
    the next good write persisted the move nobody saved."""
    before = effect_file.read_bytes()
    resp = client.put(
        "/api/map-editor/hazards/contaminated/0",
        json={**_latlng(5000, 5000), "radius": "wide"},
    )
    assert resp.status_code == 400
    assert effect_file.read_bytes() == before

    assert client.put("/api/map-editor/hazards/contaminated/1", json={"radius": 160}).status_code == 200
    bow = json.loads(effect_file.read_text())["Areas"][0]["Data"]
    assert bow["Pos"] == [13917, 0, 11178]
