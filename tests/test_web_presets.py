"""Tests for /api/presets* and /api/tweaks* (app/web/routers/presets.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_quick_tweaks import stock_elements

PASSWORD = "testpass123"

GLOBALS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<globals>
    <var name="CleanupAvoidance" type="0" value="100"/>
</globals>
"""


@pytest.fixture()
def client(sample_config, tmp_mission_dir):
    from app.web import server
    from app.web.auth import hash_password

    (tmp_mission_dir / "globals.xml").write_text(GLOBALS_XML, encoding="utf-8")

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_list_presets_includes_builtins(client: TestClient):
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    presets = resp.json()
    assert len(presets) > 0
    assert all({"id", "name", "description", "is_builtin"} <= p.keys() for p in presets)
    assert all(p["is_builtin"] for p in presets)


def test_preview_unknown_preset_404(client: TestClient):
    resp = client.post("/api/presets/does-not-exist/preview")
    assert resp.status_code == 404


def test_preview_builtin_preset(client: TestClient):
    presets = client.get("/api/presets").json()
    preset_id = presets[0]["id"]

    resp = client.post(f"/api/presets/{preset_id}/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert "changes" in body
    assert "files_affected" in body
    assert "warnings" in body


def test_apply_builtin_preset_broadcasts_configs_changed(client: TestClient):
    presets = client.get("/api/presets").json()
    preset_id = presets[0]["id"]

    resp = client.post(f"/api/presets/{preset_id}/apply")
    assert resp.status_code == 200
    assert "modified" in resp.json()


def test_apply_without_mission_dir_returns_409(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})

    presets = tclient.get("/api/presets").json()
    preset_id = presets[0]["id"]
    resp = tclient.post(f"/api/presets/{preset_id}/apply")
    assert resp.status_code == 409


def test_list_tweaks(client: TestClient):
    resp = client.get("/api/tweaks")
    assert resp.status_code == 200
    tweaks = resp.json()
    assert len(tweaks) > 0
    assert all({"id", "name", "tiers"} <= t.keys() for t in tweaks)


def test_apply_unknown_tweak_404(client: TestClient):
    resp = client.post("/api/tweaks/does-not-exist", json={"multiplier": 1.0})
    assert resp.status_code == 404


def test_apply_tweak(client: TestClient):
    tweaks = client.get("/api/tweaks").json()
    tweak_id = tweaks[0]["id"]

    resp = client.post(f"/api/tweaks/{tweak_id}", json={"multiplier": 2.0})
    assert resp.status_code == 200
    assert "modified" in resp.json()


def test_custom_preset_list_preview_and_apply(client: TestClient, sample_config):
    from app.presets.preset_storage import save_custom_preset
    from app.presets.presets import (
        EventsLayer, GameplayJsonLayer, GlobalsLayer, PresetDefinition, ServerCfgLayer, TypesLayer,
    )

    preset = PresetDefinition(
        name="My Custom",
        description="test custom preset",
        server_cfg=ServerCfgLayer(),
        gameplay_json=GameplayJsonLayer(),
        events=EventsLayer(),
        types=TypesLayer(),
        globals=GlobalsLayer(),
        recommended_mods=[],
        is_builtin=False,
    )
    path = save_custom_preset(sample_config.paths.presets_dir, preset, filename="my-custom.json")

    presets = client.get("/api/presets").json()
    custom = next(p for p in presets if p["id"] == "custom:my-custom")
    assert custom["is_builtin"] is False

    resp = client.post(f"/api/presets/{custom['id']}/preview")
    assert resp.status_code == 200

    path.unlink()


def test_custom_preset_path_traversal_rejected_over_http(client: TestClient):
    # The httpx client normalizes ".." dot-segments before the request is
    # even sent (RFC 3986), so this never reaches our handler as a "custom:.."
    # preset_id — it collapses onto an unrelated path and 405s there. Kept as
    # a smoke test that the raw traversal string can't reach a real file;
    # test_resolve_preset_rejects_traversal_stem below is the real unit check.
    resp = client.post("/api/presets/custom:../../../etc/passwd/preview")
    assert resp.status_code in (404, 405)


def test_resolve_preset_rejects_traversal_stem(sample_config):
    # Direct unit check of the validation itself: preset_id is a URL path
    # segment, so a slash-containing "stem" can't usually reach this
    # function (routing splits on "/" first) — but _resolve_preset must
    # still refuse to join a traversal stem onto the presets dir if one ever
    # does (e.g. a future route change, or literal ".." with no slash).
    from app.web.routers.presets import _resolve_preset

    assert _resolve_preset(sample_config, "custom:../../../etc/passwd") is None
    assert _resolve_preset(sample_config, "custom:..") is None
    assert _resolve_preset(sample_config, "custom:") is None


def test_requires_auth(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    tclient = TestClient(app)
    resp = tclient.get("/api/presets")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Custom presets: save / delete
# ---------------------------------------------------------------------------


def test_save_and_delete_custom_preset(client):
    resp = client.post(
        "/api/presets/custom", json={"name": "My Setup", "description": "test snapshot"}
    )
    assert resp.status_code == 200
    preset_id = resp.json()["id"]
    assert preset_id.startswith("custom:")

    ids = {p["id"] for p in client.get("/api/presets").json()}
    assert preset_id in ids

    stem = preset_id.split(":", 1)[1]
    assert client.delete(f"/api/presets/custom/{stem}").status_code == 200
    ids = {p["id"] for p in client.get("/api/presets").json()}
    assert preset_id not in ids


def test_save_custom_preset_requires_name(client):
    assert client.post("/api/presets/custom", json={"name": " "}).status_code == 422


def test_delete_custom_preset_traversal_guard(client):
    # "%2e%2e" decodes to ".." as a single path segment after routing — it
    # reaches the handler and must be rejected by the bare-filename guard.
    assert client.delete("/api/presets/custom/%2e%2e").status_code == 404
    # Encoded slashes never match the route (405/404 both fine — the handler
    # is not reached).
    assert client.delete("/api/presets/custom/..%2Fetc%2Fpasswd").status_code in (404, 405)
    assert client.delete("/api/presets/custom/nonexistent").status_code == 404


# ---------------------------------------------------------------------------
# Quick tweaks: variants (plans/35)
# ---------------------------------------------------------------------------

# BandageDressing from the stock dayzOffline.chernarusplus/db/types.xml, read
# at the pinned 1.29 commit, in a document of its own.
def bandage_types_xml() -> str:
    return stock_elements("dayzOffline.chernarusplus/db/types.xml", "type", ("BandageDressing",))


def test_list_tweaks_describes_variants(client: TestClient):
    tweaks = {t["id"]: t for t in client.get("/api/tweaks").json()}

    vehicles = tweaks["vehicle_count"]
    assert {v["key"]: v["has_events"] for v in vehicles["variants"]} == {
        "cars": True, "cars_and_parts": True, "parts": False, "motorbikes": True,
    }
    # The mission has no events.xml, so no variant links to the Map Editor.
    assert {v["map_event"] for v in vehicles["variants"]} == {""}
    assert vehicles["zero_warning"]
    assert tweaks["boat_count"]["variants"] == []
    assert tweaks["boat_count"]["has_events"] is True
    assert tweaks["boat_count"]["zero_warning"]
    assert tweaks["food_amount"]["has_events"] is False
    assert tweaks["food_amount"]["zero_warning"] == ""
    assert [v["key"] for v in tweaks["gear_amount"]["variants"]] == [
        "clothing", "containers", "medical", "tools", "weapons", "explosives", "all_gear",
    ]
    assert tweaks["zombie_count"]["variants"] == []
    assert tweaks["zombie_count"]["zero_warning"] == ""


def test_apply_tweak_unknown_variant_400(client: TestClient):
    resp = client.post("/api/tweaks/gear_amount", json={"multiplier": 2.0, "variant": "food"})
    assert resp.status_code == 400


def test_apply_tweak_with_variant(client: TestClient, tmp_mission_dir):
    types_path = tmp_mission_dir / "db" / "types.xml"
    types_path.write_text(bandage_types_xml(), encoding="utf-8")

    resp = client.post("/api/tweaks/gear_amount", json={"multiplier": 2.0, "variant": "medical"})

    assert resp.status_code == 200
    assert resp.json()["modified"] == [str(types_path)]
    assert "<nominal>80</nominal>" in types_path.read_text(encoding="utf-8")


def test_reset_tweak_endpoint(client: TestClient, tmp_mission_dir):
    types_path = tmp_mission_dir / "db" / "types.xml"
    types_path.write_text(bandage_types_xml(), encoding="utf-8")
    client.post("/api/tweaks/gear_amount", json={"multiplier": 2.0, "variant": "medical"})

    resp = client.post("/api/tweaks/gear_amount/reset", json={"variant": "medical"})

    assert resp.status_code == 200
    assert resp.json()["modified"] == [str(types_path)]
    assert "<nominal>40</nominal>" in types_path.read_text(encoding="utf-8")


def test_reset_tweak_without_a_body_resets_the_first_variant(client: TestClient, tmp_mission_dir):
    (tmp_mission_dir / "db" / "types.xml").write_text(bandage_types_xml(), encoding="utf-8")

    resp = client.post("/api/tweaks/gear_amount/reset", json={})

    assert resp.status_code == 200
    assert resp.json()["modified"] == []  # nothing tweaked yet, so nothing to put back


def test_reset_tweak_rejects_unknown_tweak_and_variant(client: TestClient):
    assert client.post("/api/tweaks/does-not-exist/reset", json={}).status_code == 404
    assert client.post("/api/tweaks/gear_amount/reset", json={"variant": "food"}).status_code == 400


def test_saving_a_clashing_custom_preset_is_409(client: TestClient):
    """Test pass 2, N26: "Zz Test" then "zz test!" silently replaced the first."""
    assert client.post("/api/presets/custom", json={"name": "Zz Test"}).status_code == 200
    resp = client.post("/api/presets/custom", json={"name": "zz test!"})
    assert resp.status_code == 409
    assert "Zz Test" in resp.json()["detail"]
    # The same name again is an update, not a clash.
    assert client.post("/api/presets/custom", json={"name": "Zz Test"}).status_code == 200


def test_motorbike_variant_links_to_the_map_editor_when_the_mission_has_the_event(client, tmp_mission_dir):
    from tests.conftest import CE_1_30

    (tmp_mission_dir / "db" / "events.xml").write_text(
        stock_elements("dayzOffline.chernarusplus/db/events.xml", "event", ("VehicleMotorbike",), CE_1_30),
        encoding="utf-8",
    )
    vehicles = {t["id"]: t for t in client.get("/api/tweaks").json()}["vehicle_count"]
    assert {v["key"]: v["map_event"] for v in vehicles["variants"]} == {
        "cars": "", "cars_and_parts": "", "parts": "", "motorbikes": "VehicleMotorbike",
    }


def test_an_unreadable_events_file_drops_the_map_link(client, tmp_mission_dir):
    (tmp_mission_dir / "db" / "events.xml").write_text("<events><event", encoding="utf-8")
    vehicles = {t["id"]: t for t in client.get("/api/tweaks").json()}["vehicle_count"]
    assert {v["map_event"] for v in vehicles["variants"]} == {""}
