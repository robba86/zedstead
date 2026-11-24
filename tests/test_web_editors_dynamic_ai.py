"""Tests for app/web/editors/dynamic_ai.py and GET /api/dynamic-ai (plans/47 C)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.ops import dynamic_ai as ops
from app.web import editors
from tests.conftest import write_test_mods
from tests.test_dynamic_ai import (  # the same fixture install
    MOD,
    _group,
    _location,
    _point,
    _settings,
    _write,
    dyn,  # noqa: F401 - pytest fixture
)

PASSWORD = "s3cret-pass"


def _spec(editor_id: str):
    spec = editors.get_editor(editor_id)
    assert spec is not None, editor_id
    return spec


def _doc(cfg, name="SpatialSettings.json") -> dict:
    return json.loads((ops.settings_dir(cfg) / name).read_text())


def _fields(body: dict) -> dict[str, dict]:
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


def test_six_editors_in_their_own_group():
    ids = sorted(s.id for s in editors.list_editors() if s.group == "Dynamic AI")
    assert ids == [
        "dynai-audio", "dynai-groups", "dynai-locations", "dynai-notifications",
        "dynai-points", "dynai-settings",
    ]


def test_nothing_exists_before_the_mod_writes_its_files(sample_config):
    for spec in editors.list_editors():
        if spec.group == "Dynamic AI":
            assert spec.exists(sample_config) is False
            assert spec.load(sample_config)["kind"] in ("form", "table")


def test_the_player_database_is_never_an_edit_target(dyn):
    names = {t.name for s in editors.list_editors() if s.group == "Dynamic AI" for t in s.raw_targets(dyn)}
    assert names == {"SpatialSettings.json", "Notificationsettings.json"}


# ── the settings form ──────────────────────────────────────────────


def test_the_form_groups_fields_and_leaves_out_version_and_the_lists(dyn):
    doc = _settings()
    doc["SomethingNew"] = 7
    _write(dyn, doc)
    fields = _fields(_spec("dynai-settings").load(dyn))
    assert "Version" not in fields and "Group" not in fields
    assert fields["HuntMode"]["choices"] == [str(n) for n in range(9)]
    assert fields["MinDistance"]["type"] == "int"
    assert fields["SomethingNew"]["value"] == 7  # a key the next release adds


def test_a_form_save_patches_and_keeps_whole_numbers_whole(dyn):
    doc = _settings(Spatial_InVehicle=0, Group=[_group("Keep", faction="East")])
    doc["Unknown"] = {"nested": True}
    _write(dyn, doc)
    _spec("dynai-settings").save(dyn, {
        "MinDistance": "150", "Spatial_MinTimer": "7.5", "Spatial_InVehicle": True,
        "Version": "99", "NotInFile": "x",
    })
    saved = _doc(dyn)
    assert saved["MinDistance"] == 150 and isinstance(saved["MinDistance"], int)
    assert saved["Spatial_MinTimer"] == 7.5
    assert saved["Spatial_InVehicle"] == 1  # the file had 0/1, so it stays an int
    assert saved["Version"] == 21 and "NotInFile" not in saved
    assert saved["Unknown"] == {"nested": True}
    assert saved["Group"][0]["Spatial_Name"] == "Keep"


@pytest.mark.parametrize(
    "values, message",
    [
        ({"MaxAI": "4.5"}, "not a whole number"),
        ({"MaxAI": ""}, "can't be left empty"),
        ({"HuntMode": "12"}, "not one of"),
    ],
)
def test_a_form_save_is_refused_with_the_field_named(dyn, values, message):
    before = _doc(dyn)
    with pytest.raises(ValueError, match=message) as exc:
        _spec("dynai-settings").save(dyn, values)
    assert next(iter(values)) in str(exc.value)
    assert _doc(dyn) == before


# ── the tables ──────────────────────────────────────────────────────


def test_group_table_offers_the_installs_factions_and_loadouts(dyn):
    body = _spec("dynai-groups").load(dyn)
    columns = {c["key"]: c for c in body["columns"]}
    # RANDOM first: Expansion picks a registered faction for it (plans/64 D2).
    assert columns["Spatial_Faction"]["choices"] == ["RANDOM", "East", "West"]
    assert columns["Spatial_Loadout"]["choices"] == ["EastLoadout.json", "WestLoadout.json"]
    assert body["rows"][0]["values"]["Spatial_Name"] == "Guard"
    assert body["rows"][0]["choices"]["Spatial_Faction"] == ["RANDOM", "East", "West"]
    assert body["duplicate"] is True and body["add_row"] is True


def test_group_save_edits_adds_duplicates_and_removes(dyn):
    first = _group("A")
    first["FutureKey"] = "kept"
    _write(dyn, _settings(Group=[first, _group("B")]))
    rows = _spec("dynai-groups").load(dyn)["rows"]
    rows[0]["values"]["Spatial_MaxCount"] = "5"
    dup = {"id": "dup-0-123", "values": {**rows[0]["values"], "Spatial_Name": "A2"}}
    new = {"id": "new-1-1", "values": {"Spatial_Name": "Fresh", "Spatial_Faction": "West",
                                       "Spatial_Loadout": "WestLoadout.json"}}
    _spec("dynai-groups").save(dyn, [rows[0], dup, new])  # B removed
    groups = _doc(dyn)["Group"]
    assert [g["Spatial_Name"] for g in groups] == ["A", "A2", "Fresh"]
    assert groups[0]["Spatial_MaxCount"] == 5 and groups[0]["FutureKey"] == "kept"
    assert groups[1]["FutureKey"] == "kept"  # a duplicate carries what the table hides
    assert groups[2]["Spatial_MinCount"] == 1 and "FutureKey" not in groups[2]


def test_a_table_save_refuses_a_fraction_and_writes_4_point_0_as_4(dyn):
    rows = _spec("dynai-groups").load(dyn)["rows"]
    rows[0]["values"]["Spatial_MaxCount"] = "4.5"
    with pytest.raises(ValueError, match="Guard, Spatial_MaxCount"):
        _spec("dynai-groups").save(dyn, rows)
    # Typed as 4.0, it is written as the whole number the mod needs.
    rows[0]["values"]["Spatial_MaxCount"] = "4.0"
    _spec("dynai-groups").save(dyn, rows)
    assert json.dumps(_doc(dyn)["Group"][0]["Spatial_MaxCount"]) == "4"


def test_points_keep_their_positions_and_are_only_duplicated(dyn):
    _write(dyn, _settings(Point=[_point("P", Spatial_Position=[100.0, 5.0, 200.0])]))
    body = _spec("dynai-points").load(dyn)
    assert body["add_row"] is False and body["warnings"]
    assert "Spatial_Position" not in {c["key"] for c in body["columns"]}
    row = body["rows"][0]
    assert row["values"]["Spatial_ZoneLoadout"] == ["EastLoadout.json"]
    row["values"]["Spatial_Radius"] = "250"
    dup = {"id": "dup-0-9", "values": {**row["values"], "Spatial_Name": "P2"}}
    _spec("dynai-points").save(dyn, [row, dup])
    points = _doc(dyn)["Point"]
    assert [p["Spatial_Position"] for p in points] == [[100.0, 5.0, 200.0]] * 2
    assert points[0]["Spatial_Radius"] == 250.0
    with pytest.raises(ValueError, match="only be duplicated"):
        _spec("dynai-points").save(dyn, [{"id": "new-0-1", "values": row["values"]}])


def test_locations_round_trip_unchanged(dyn):
    _write(dyn, _settings(Location=[_location()]))
    before = _doc(dyn)
    rows = _spec("dynai-locations").load(dyn)["rows"]
    _spec("dynai-locations").save(dyn, rows)
    assert _doc(dyn) == before


def test_notifications_table_round_trips_its_message_lines(dyn):
    notes = {"Version": 2, "notification": [{
        "Spatial_Name": "Guard", "StartTime": 0.0, "StopTime": 24.0, "AgeTime": 0.0,
        "MessageType": 1, "MessageTitle": "t", "MessageText": ["one", "two"],
    }]}
    _write(dyn, notes, name="Notificationsettings.json")
    body = _spec("dynai-notifications").load(dyn)
    columns = {c["key"]: c for c in body["columns"]}
    assert columns["Spatial_Name"]["choices"] == ["Guard"]  # the group names
    rows = body["rows"]
    rows[0]["values"]["MessageText"] = '["one", "two", "three"]'
    _spec("dynai-notifications").save(dyn, rows)
    saved = _doc(dyn, "Notificationsettings.json")
    assert saved["notification"][0]["MessageText"] == ["one", "two", "three"]
    assert saved["Version"] == 2


# ── the Raw tab and the API ─────────────────────────────────────────


@pytest.fixture()
def client(dyn):
    from app.web import server
    from app.web.auth import hash_password

    dyn.web_password_hash = hash_password(PASSWORD)
    c = TestClient(server.create_app(dyn))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_the_raw_tab_refuses_a_decimal_in_a_whole_number_field(client, dyn):
    bad = json.dumps(_settings(Group=[_group(hi=4.0)]))
    resp = client.put("/api/configs/dynai-settings/raw", json={"target": "SpatialSettings.json", "content": bad})
    assert resp.status_code == 400
    assert "Group[0].Spatial_MaxCount = 4.0" in resp.json()["detail"]
    assert _doc(dyn)["Group"][0]["Spatial_MaxCount"] == 3
    good = json.dumps(_settings(MaxAI=10))
    resp = client.put("/api/configs/dynai-settings/raw", json={"target": "SpatialSettings.json", "content": good})
    assert resp.status_code == 200 and _doc(dyn)["MaxAI"] == 10


def test_the_report_route(client, dyn):
    body = client.get("/api/dynamic-ai").json()
    assert body["installed"] is True and body["mod"] == MOD
    assert body["factions"] == ["East", "West"] and body["findings"] == []
    write_test_mods(dyn, ['@CF'])
    assert client.get("/api/dynamic-ai").json() == {"installed": False}


def test_the_table_editors_through_the_api(client, dyn):
    body = client.get("/api/configs/dynai-groups").json()
    body["rows"][0]["values"]["Spatial_Name"] = "Renamed"
    resp = client.put("/api/configs/dynai-groups", json={"rows": body["rows"]})
    assert resp.status_code == 200
    assert _doc(dyn)["Group"][0]["Spatial_Name"] == "Renamed"
    body["rows"][0]["values"]["Spatial_MinCount"] = "1.5"
    resp = client.put("/api/configs/dynai-groups", json={"rows": body["rows"]})
    assert resp.status_code == 400 and "whole number" in resp.json()["detail"]
