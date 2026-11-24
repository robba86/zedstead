"""Tests for app/web/editors/radzone.py and GET /api/radzone (plans/48 B)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.ops import zone_mods as ops
from app.web import editors
from tests.conftest import write_test_mods
from tests.test_zone_mods import (  # the same fixture install
    MOD,
    _area,
    _crates,
    _radiation,
    _write,
    _zone,
    rz,  # noqa: F401 - pytest fixture
)

PASSWORD = "s3cret-pass"


def _spec(editor_id: str):
    spec = editors.get_editor(editor_id)
    assert spec is not None, editor_id
    return spec


def _doc(cfg, name=ops.RADIATION) -> dict:
    return json.loads((ops.config_dir(cfg) / name).read_text())


def _fields(body: dict) -> dict[str, dict]:
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


def test_five_editors_in_their_own_group():
    ids = sorted(s.id for s in editors.list_editors() if s.group == "RadZone")
    assert ids == [
        "radzone-clothes", "radzone-crate-areas", "radzone-crates", "radzone-settings",
        "radzone-zones",
    ]


def test_nothing_exists_before_the_mod_writes_its_files(sample_config):
    for spec in editors.list_editors():
        if spec.group == "RadZone":
            assert spec.exists(sample_config) is False
            assert spec.load(sample_config)["kind"] in ("form", "table")


def test_only_the_three_config_files_are_edit_targets(rz):
    names = {t.name for s in editors.list_editors() if s.group == "RadZone" for t in s.raw_targets(rz)}
    assert names == {ops.RADIATION, ops.CRATES, ops.CLOTHES}


# ── forms ─────────────────────────────────────────────────────────────


def test_the_settings_form_leaves_the_zone_list_to_its_table(rz):
    fields = _fields(_spec("radzone-settings").load(rz))
    assert "RadiationAreaLocation" not in fields
    assert fields["IsRadiationZoneActive"]["type"] == "bool"
    assert fields["RequiredProtection"]["type"] == "int"
    # Undocumented keys get a field, never an invented explanation.
    assert "help" not in fields["NaturalRadDecayPerMin"]


def test_a_settings_save_patches_and_keeps_whole_numbers_whole(rz):
    _spec("radzone-settings").save(rz, {
        "CriticalProtection": "80", "RadChanceVomit": "0.3",
        "IsRadiationZoneActive": False, "RadiationAreaLocation": "[]", "NotInFile": "x",
    })
    doc = _doc(rz)
    assert doc["CriticalProtection"] == 80 and isinstance(doc["CriticalProtection"], int)
    assert doc["RadChanceVomit"] == 0.3
    assert doc["IsRadiationZoneActive"] == 0  # the file's 0/1 form, not a bool
    assert len(doc["RadiationAreaLocation"]) == 1  # the form does not own the list
    assert "NotInFile" not in doc
    with pytest.raises(ValueError, match="RequiredProtection: .*whole number"):
        _spec("radzone-settings").save(rz, {"RequiredProtection": "5.5"})


def test_a_float_is_shown_without_its_float32_noise(rz):
    """RadZone writes Enforce's 32-bit floats as doubles: 0.2 is on disk as
    0.20000000298023224. The form shows 0.2, which DayZ loads as the same float."""
    doc = _radiation()
    doc["RadChanceVomit"] = 0.20000000298023224
    doc["ClothesQuantityMultiplier"] = 0.15000000596046448
    doc["RadiationAreaLocation"][0]["X"] = 4623.64013671875
    _write(rz, ops.RADIATION, doc)
    fields = _fields(_spec("radzone-settings").load(rz))
    assert fields["RadChanceVomit"]["value"] == 0.2
    assert fields["ClothesQuantityMultiplier"]["value"] == 0.15
    assert fields["RadHealthDamage"]["value"] == 1.0
    assert _spec("radzone-zones").load(rz)["rows"][0]["values"]["X"] == 4623.64
    # An int field is never touched by it.
    assert fields["CriticalProtection"]["value"] == 70


def test_float32_display_never_changes_what_dayz_loads():
    import struct

    from app.web.editors._mod_json import float32_display

    f32 = lambda v: struct.pack("<f", v)  # noqa: E731
    for value in (0.20000000298023224, 4623.64013671875, 123456789.0, 0.1, 1e-7, 3.4e38):
        assert f32(float32_display(value)) == f32(value), value
    assert float32_display(1e300) == 1e300  # beyond float32: shown as is
    assert float32_display(True) is True and float32_display(5) == 5


def test_the_crate_form_keeps_the_areas_and_loot_lists(rz):
    _spec("radzone-crates").save(rz, {"TimerLoot": "600000", "AreaLocation": "[]"})
    doc = _doc(rz, ops.CRATES)
    assert doc["TimerLoot"] == 600000
    assert doc["AreaLocation"][0]["RZName"] == "North"
    assert doc["ListLoots"] == [{"Name": "Army", "Loots": []}]


# ── tables ────────────────────────────────────────────────────────────


def test_a_zone_row_edit_patches_that_zone_only(rz):
    doc = _radiation(_zone("A"), _zone("B", x=100.0))
    doc["RadiationAreaLocation"][1]["FutureKey"] = [1, 2]
    _write(rz, ops.RADIATION, doc)
    body = _spec("radzone-zones").load(rz)
    assert [r["values"]["RadiationZoneStatut"] for r in body["rows"]] == ["A", "B"]
    assert body["add_row"] is False and body["warnings"]
    rows = body["rows"]
    rows[1]["values"]["X"] = "250.5"
    rows[1]["values"]["NbSickGivenForRadiation"] = "70"
    _spec("radzone-zones").save(rz, rows)
    zones = _doc(rz)["RadiationAreaLocation"]
    assert zones[1]["X"] == 250.5 and zones[1]["NbSickGivenForRadiation"] == 70
    assert zones[1]["FutureKey"] == [1, 2]
    assert zones[0] == _zone("A")


def test_a_new_zone_is_a_copy_never_a_blank_row(rz):
    rows = _spec("radzone-zones").load(rz)["rows"]
    copy = {"id": "dup-0-x", "values": {**rows[0]["values"], "RadiationZoneStatut": "Copy"}}
    _spec("radzone-zones").save(rz, [*rows, copy])
    zones = _doc(rz)["RadiationAreaLocation"]
    assert [z["RadiationZoneStatut"] for z in zones] == ["North", "Copy"]
    assert zones[1]["X"] == zones[0]["X"]
    with pytest.raises(ValueError, match="duplicated"):
        _spec("radzone-zones").save(rz, [*rows, {"id": "new-1", "values": {"RadiationZoneStatut": "Blank"}}])


def test_a_whole_number_column_refuses_a_decimal_naming_the_zone(rz):
    rows = _spec("radzone-zones").load(rz)["rows"]
    rows[0]["values"]["NbSickGivenForRadiation"] = "50.5"
    with pytest.raises(ValueError, match="North, NbSickGivenForRadiation"):
        _spec("radzone-zones").save(rz, rows)


def test_a_crate_area_rename_keeps_its_crates_and_offers_the_zone_names(rz):
    _write(rz, ops.RADIATION, _radiation(_zone("North"), _zone("South", x=100.0)))
    _write(rz, ops.CRATES, _crates(_area("North", (5000.0, 1.0, 6000.0), (5001.0, 1.0, 6000.0))))
    body = _spec("radzone-crate-areas").load(rz)
    assert body["columns"][0]["choices"] == ["North", "South"]
    rows = body["rows"]
    rows[0]["values"]["RZName"] = "South"
    _spec("radzone-crate-areas").save(rz, rows)
    area = _doc(rz, ops.CRATES)["AreaLocation"][0]
    assert area["RZName"] == "South"
    assert len(area["RZCrateLocations"]) == 2


def _with_nested(row: dict, **edits) -> dict:
    """*row* as the table sends it after the tree under it was edited."""
    base = {k: row["nested"][k] for k in edits}
    return {**row, "values": {**row["values"], "$nested": edits, "$nested_base": base}}


def test_the_clothes_table_edits_its_list_as_a_tree(rz):
    protections = [{"ClotheName": "GasMask", "Protection": 1}, {"ClotheName": "NBCHood", "Protection": 1}]
    _write(rz, ops.CLOTHES, {"RZListSlotProtection": [{"SlotName": "Mask", "ListProtections": protections}]})
    body = _spec("radzone-clothes").load(rz)
    assert [c["key"] for c in body["columns"]] == ["SlotName"]
    row = body["rows"][0]
    assert row["nested"] == {"ListProtections": protections}
    # Text from the tree's number box; a third entry copied from the last one.
    edited = [{"ClotheName": "GasMask", "Protection": "2"}, protections[1],
              {"ClotheName": "Respirator", "Protection": "1"}]
    _spec("radzone-clothes").save(rz, [_with_nested(row, ListProtections=edited)])
    saved = _doc(rz, ops.CLOTHES)["RZListSlotProtection"][0]["ListProtections"]
    assert saved == [{"ClotheName": "GasMask", "Protection": 2}, protections[1],
                     {"ClotheName": "Respirator", "Protection": 1}]
    assert all(isinstance(p["Protection"], int) for p in saved)
    with pytest.raises(ValueError, match=r"Mask: ListProtections\[1\]\.Protection: .*whole number"):
        bad = [{"ClotheName": "GasMask", "Protection": "1.5"}]
        _spec("radzone-clothes").save(rz, [_with_nested(_spec("radzone-clothes").load(rz)["rows"][0],
                                                        ListProtections=bad)])


def test_a_crate_area_s_crates_are_its_row_s_tree(rz):
    body = _spec("radzone-crate-areas").load(rz)
    row = body["rows"][0]
    assert set(row["nested"]) == {"RZCrateLocations", "CreaturesLists"}
    crates = row["nested"]["RZCrateLocations"]
    moved = [{**crates[0], "POS": [5100.0, "100", 6100.0]}]
    _spec("radzone-crate-areas").save(rz, [_with_nested(row, RZCrateLocations=moved)])
    area = _doc(rz, ops.CRATES)["AreaLocation"][0]
    assert area["RZCrateLocations"][0]["POS"] == [5100.0, 100.0, 6100.0]
    assert area["RZCrateLocations"][0]["RZMaxLoot"] == 5
    assert area["RZName"] == "North" and area["CreaturesLists"] == []


def test_a_tree_edit_over_a_changed_file_is_refused(rz):
    row = _spec("radzone-crate-areas").load(rz)["rows"][0]
    _write(rz, ops.CRATES, _crates(_area("North", (1.0, 2.0, 3.0))))  # someone else's save
    moved = [{**row["nested"]["RZCrateLocations"][0], "POS": [9.0, 9.0, 9.0]}]
    with pytest.raises(ValueError, match="North: RZCrateLocations changed in the file"):
        _spec("radzone-crate-areas").save(rz, [_with_nested(row, RZCrateLocations=moved)])
    assert _doc(rz, ops.CRATES)["AreaLocation"][0]["RZCrateLocations"][0]["POS"] == [1.0, 2.0, 3.0]


def test_a_duplicated_area_takes_its_copied_tree(rz):
    row = _spec("radzone-crate-areas").load(rz)["rows"][0]
    moved = [{**row["nested"]["RZCrateLocations"][0], "POS": [7.0, 8.0, 9.0]}]
    copy = _with_nested({**row, "id": "dup-0-1"}, RZCrateLocations=moved)
    _spec("radzone-crate-areas").save(rz, [row, copy])
    areas = _doc(rz, ops.CRATES)["AreaLocation"]
    assert areas[0]["RZCrateLocations"][0]["POS"] == [5000.0, 100.0, 6000.0]
    assert areas[1]["RZCrateLocations"][0]["POS"] == [7.0, 8.0, 9.0]


def test_a_zone_row_carries_any_nested_part_and_no_scalar_list(rz):
    doc = _radiation(_zone("A"))
    doc["RadiationAreaLocation"][0]["FutureKey"] = [1, 2]
    doc["RadiationAreaLocation"][0]["FutureParts"] = [{"N": 1}]
    _write(rz, ops.RADIATION, doc)
    row = _spec("radzone-zones").load(rz)["rows"][0]
    assert row["nested"] == {"FutureParts": [{"N": 1}]}
    # A key the editor does not hand out is ignored.
    sent = {**row, "values": {**row["values"], "$nested": {"FutureKey": [3]}, "$nested_base": {"FutureKey": [1, 2]}}}
    _spec("radzone-zones").save(rz, [sent])
    assert _doc(rz)["RadiationAreaLocation"][0]["FutureKey"] == [1, 2]


# ── through the API ───────────────────────────────────────────────────


@pytest.fixture()
def client(rz):
    from app.web import server
    from app.web.auth import hash_password

    rz.web_password_hash = hash_password(PASSWORD)
    c = TestClient(server.create_app(rz))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_the_raw_tab_refuses_a_decimal_in_a_whole_number_field(client, rz):
    doc = _crates()
    doc["TimerLoot"] = 2700000.0
    resp = client.put(
        "/api/configs/radzone-crates/raw", json={"target": ops.CRATES, "content": json.dumps(doc)}
    )
    assert resp.status_code == 400
    assert "TimerLoot = 2700000.0" in resp.json()["detail"]
    good = _crates()
    good["TimerLoot"] = 60000
    resp = client.put(
        "/api/configs/radzone-crates/raw", json={"target": ops.CRATES, "content": json.dumps(good)}
    )
    assert resp.status_code == 200 and _doc(rz, ops.CRATES)["TimerLoot"] == 60000


def test_the_report_route(client, rz):
    body = client.get("/api/radzone").json()
    assert body["installed"] is True and body["mod"] == MOD
    assert body["findings"] == []
    assert [z["name"] for z in body["zones"]] == ["North"]
    write_test_mods(rz, ['@CF'])
    assert client.get("/api/radzone").json() == {"installed": False}


def test_the_zone_table_through_the_api(client, rz):
    body = client.get("/api/configs/radzone-zones").json()
    body["rows"][0]["values"]["Radius"] = "450"
    resp = client.put("/api/configs/radzone-zones", json={"rows": body["rows"]})
    assert resp.status_code == 200
    assert _doc(rz)["RadiationAreaLocation"][0]["Radius"] == 450.0
    body["rows"][0]["values"]["IsSquareZone"] = "maybe"
    body["rows"][0]["values"]["NbSickGivenForRadiation"] = "1.5"
    resp = client.put("/api/configs/radzone-zones", json={"rows": body["rows"]})
    assert resp.status_code == 400 and "whole number" in resp.json()["detail"]



def test_the_loot_lists_are_a_tree_on_the_crate_form(rz):
    spec = _spec("radzone-crates")
    base = spec.load(rz)["nested"]
    assert base == {"ListLoots": [{"Name": "Army", "Loots": []}]}
    loots = [*base["ListLoots"], {"Name": "Medic", "Loots": []}]
    spec.save(rz, {"$nested": {"ListLoots": loots}, "$nested_base": base})
    doc = _doc(rz, ops.CRATES)
    assert [p["Name"] for p in doc["ListLoots"]] == ["Army", "Medic"]
    assert doc["AreaLocation"][0]["RZName"] == "North"
