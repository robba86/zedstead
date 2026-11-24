"""Expansion AI's Patrols table (plans/64 D1, step 5)."""

from __future__ import annotations

import json

import pytest

from app.ops import expansion_ai as ops
from app.web import editors
from tests.test_expansion_ai import (  # the same fixture install
    _object_patrol,
    _patrol,
    _settings,
    _write,
    eai,  # noqa: F401 - pytest fixture
)

ID = "expansion-ai-patrol-list"


def _spec():
    return editors.get_editor(ID)


def _doc(cfg) -> dict:
    return json.loads(ops.patrol_path(cfg).read_text(encoding="utf-8"))


def test_the_table_lives_in_the_expansion_group(eai):
    spec = _spec()
    assert (spec.group, spec.kind, spec.title) == ("Expansion", "table", "Expansion AI Patrol List")
    assert spec.exists(eai) is True
    assert [t.name for t in spec.raw_targets(eai)] == ["AIPatrolSettings.json"]


def test_positions_and_the_object_class_are_not_columns(eai):
    body = _spec().load(eai)
    keys = [c["key"] for c in body["columns"]]
    assert "Waypoints" not in keys and "ObjectClassName" not in keys and "Units" not in keys
    assert body["add_row"] is False and len(body["warnings"]) == 2
    assert len(body["rows"]) == 5


def test_the_pickers_come_from_the_install_and_the_file(eai):
    choices = _spec().load(eai)["rows"][0]["choices"]
    assert choices["Faction"] == ["RANDOM", "East", "Guards", "West"]
    # Expansion adds .json itself; empty is the faction's default.
    assert choices["Loadout"] == ["", "FreshSpawnLoadout", "NBCLoadout", "PoliceLoadout"]
    assert choices["LoadBalancingCategory"][0] == "" and "Global" in choices["LoadBalancingCategory"]
    assert choices["LootDropOnDeath"] == ["", "Example"]
    assert "ROAMING" in choices["Behaviour"] and "JOG" in choices["Speed"]


def test_a_save_that_changes_nothing_changes_nothing(eai):
    path = ops.patrol_path(eai)
    before = path.read_text(encoding="utf-8")
    _spec().save(eai, _spec().load(eai)["rows"])
    after = path.read_text(encoding="utf-8")
    assert json.loads(after) == json.loads(before)
    for old, new in zip(json.loads(before)["Patrols"], json.loads(after)["Patrols"]):
        assert {k: type(v) for k, v in new.items()} == {k: type(v) for k, v in old.items()}


def test_an_unticked_box_does_not_add_its_key(eai):
    """The fixture's patrols have no CanBeLooted (the real v32 file does)."""
    rows = _spec().load(eai)["rows"]
    assert rows[0]["values"]["CanBeLooted"] is False
    rows[1]["values"]["CanBeLooted"] = True
    _spec().save(eai, rows)
    patrols = _doc(eai)["Patrols"]
    assert "CanBeLooted" not in patrols[0] and patrols[1]["CanBeLooted"] == 1


def test_an_edit_changes_only_its_cell(eai):
    _write(eai, _settings(Patrols=[_patrol(), _object_patrol()]))
    rows = _spec().load(eai)["rows"]
    before = _doc(eai)
    rows[0]["values"]["NumberOfAI"] = "4"
    rows[0]["values"]["Persist"] = False
    _spec().save(eai, rows)
    after = _doc(eai)
    assert after["Patrols"][0]["NumberOfAI"] == 4
    assert after["Patrols"][0]["Persist"] == 0  # 0/1, the way the file has it
    after["Patrols"][0].update(NumberOfAI=before["Patrols"][0]["NumberOfAI"], Persist=1)
    assert after == before


def test_a_duplicate_keeps_waypoints_units_and_unknown_keys(eai):
    patrol = _object_patrol(FutureKey="kept", Units=["a"])
    _write(eai, _settings(Patrols=[patrol]))
    row = _spec().load(eai)["rows"][0]
    dup = {"id": "dup-0-7", "values": {**row["values"], "Faction": "West"}}
    _spec().save(eai, [row, dup])
    patrols = _doc(eai)["Patrols"]
    assert [p["Faction"] for p in patrols] == ["Guards", "West"]
    for p in patrols:
        assert p["Waypoints"] == patrol["Waypoints"] and p["Units"] == ["a"]
        assert p["FutureKey"] == "kept" and p["ObjectClassName"] == "Land_Mil_GuardTower"


def test_a_blank_row_is_refused(eai):
    row = _spec().load(eai)["rows"][0]
    with pytest.raises(ValueError, match="only be duplicated"):
        _spec().save(eai, [{"id": "new-0-1", "values": row["values"]}])


def test_a_fraction_in_a_whole_number_is_refused_with_the_field_named(eai):
    _write(eai, _settings(Patrols=[_patrol()]))
    rows = _spec().load(eai)["rows"]
    rows[0]["values"]["NumberOfAI"] = "4.5"
    with pytest.raises(ValueError, match="Survivors, NumberOfAI: '4.5' is not a whole number"):
        _spec().save(eai, rows)


def test_a_value_outside_a_picker_is_kept(eai):
    """The pickers offer what the install has; the check, not the save, judges."""
    _write(eai, _settings(Patrols=[_patrol(Faction="Bandits", Speed="HOVER")]))
    rows = _spec().load(eai)["rows"]
    assert rows[0]["values"]["Faction"] == "Bandits"
    _spec().save(eai, rows)
    assert (_doc(eai)["Patrols"][0]["Faction"], _doc(eai)["Patrols"][0]["Speed"]) == ("Bandits", "HOVER")


def test_with_no_mission_the_table_is_empty_and_a_save_says_why(eai, monkeypatch):
    from app.xml import xml_utils

    monkeypatch.setattr(xml_utils, "get_mission_dir", lambda config: None)
    spec = _spec()
    assert spec.exists(eai) is False
    assert spec.load(eai) == {"kind": "table", "columns": [], "rows": []}
    assert spec.raw_targets(eai) == []
    with pytest.raises(RuntimeError, match="No mission is selected"):
        spec.save(eai, [])


def test_the_api_round_trip(eai):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    eai.web_password_hash = hash_password("patrol-table-test")
    client = TestClient(server.create_app(eai))
    assert client.post("/api/auth/login", json={"password": "patrol-table-test"}).status_code == 200
    body = client.get(f"/api/configs/{ID}").json()
    body["rows"][0]["values"]["Chance"] = "0"
    assert client.put(f"/api/configs/{ID}", json={"rows": body["rows"]}).status_code == 200
    assert _doc(eai)["Patrols"][0]["Chance"] == 0.0
    ids = [f["id"] for f in client.get("/api/expansion-ai").json()["findings"]]
    assert ids == ["expansion_ai_never_spawns"]
    body["rows"][0]["values"]["NumberOfAI"] = "1.5"
    resp = client.put(f"/api/configs/{ID}", json={"rows": body["rows"]})
    assert resp.status_code == 400 and "NumberOfAI" in resp.json()["detail"]
