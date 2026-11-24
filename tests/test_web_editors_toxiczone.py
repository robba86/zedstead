"""Tests for app/web/editors/toxiczone.py and /api/toxiczone (plans/48,
ToxicZone pass B)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.ops import toxic_zone as ops
from app.web import editors
from tests.conftest import write_test_mods
from tests.test_toxic_zone import (  # the same fixture install
    MOD,
    _moving,
    _moving_zone,
    _static,
    _static_zone,
    _write,
    tzc,  # noqa: F401 - pytest fixture
)

PASSWORD = "s3cret-pass"
ADMIN = "76561190000000001"


def _spec(editor_id: str):
    spec = editors.get_editor(editor_id)
    assert spec is not None, editor_id
    return spec


def _doc(cfg, name) -> dict:
    return json.loads((ops.config_dir(cfg) / name).read_text())


def _fields(body: dict) -> dict[str, dict]:
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


def test_eight_editors_in_their_own_group():
    ids = sorted(s.id for s in editors.list_editors() if s.group == "ToxicZone")
    assert ids == [
        "toxiczone-clothes", "toxiczone-loot", "toxiczone-moving", "toxiczone-moving-zones",
        "toxiczone-protection", "toxiczone-settings", "toxiczone-static", "toxiczone-static-zones",
    ]


def test_nothing_exists_before_the_mod_writes_its_files(sample_config):
    for spec in editors.list_editors():
        if spec.group == "ToxicZone":
            assert spec.exists(sample_config) is False
            assert spec.load(sample_config)["kind"] in ("form", "table")


def test_only_the_five_config_files_are_edit_targets(tzc):
    names = {t.name for s in editors.list_editors() if s.group == "ToxicZone" for t in s.raw_targets(tzc)}
    assert names == set(ops.FILES)


# ── forms ─────────────────────────────────────────────────────────────


def test_the_settings_save_keeps_whole_numbers_whole(tzc):
    _spec("toxiczone-settings").save(tzc, {"NbSickGiven": "40.0"})
    assert _doc(tzc, ops.GENERAL)["NbSickGiven"] == 40
    with pytest.raises(ValueError, match="NbSickGiven"):
        _spec("toxiczone-settings").save(tzc, {"NbSickGiven": "4.5"})


def test_the_moving_form_holds_the_switch_and_admin_list_not_the_zones(tzc):
    fields = _fields(_spec("toxiczone-moving").load(tzc))
    assert set(fields) == {"IsMovingZoneActive", "AdminUID"}
    _spec("toxiczone-moving").save(tzc, {"AdminUID": f'["{ADMIN}", "76561190000000002"]',
                                         "MovingToxicAreaLocation": "[]"})
    doc = _doc(tzc, ops.MOVING)
    assert doc["AdminUID"] == [ADMIN, "76561190000000002"]
    assert len(doc["MovingToxicAreaLocation"]) == 1


def test_the_forms_leave_their_lists_alone(tzc):
    for editor_id, name, key in (
        ("toxiczone-static", ops.STATIC, "StaticToxicAreaLocation"),
        ("toxiczone-loot", ops.LOOT, "ListLoots"),
        ("toxiczone-protection", ops.CLOTHES, "TZListSlotProtection"),
    ):
        before = _doc(tzc, name)[key]
        assert key not in _fields(_spec(editor_id).load(tzc))
        _spec(editor_id).save(tzc, {key: "[]"})
        assert _doc(tzc, name)[key] == before


# ── static zones ──────────────────────────────────────────────────────


def test_a_static_zone_edit_keeps_its_particles_and_crates(tzc):
    body = _spec("toxiczone-static-zones").load(tzc)
    assert "ParticlesPosition" not in body["rows"][0]["values"]
    body["rows"][0]["values"]["Radius"] = "450"
    _spec("toxiczone-static-zones").save(tzc, body["rows"])
    zone = _doc(tzc, ops.STATIC)["StaticToxicAreaLocation"][0]
    assert zone["Radius"] == 450.0
    assert zone["ParticlesPosition"] == _static_zone()["ParticlesPosition"]
    assert zone["ToxicCratePosition"] == _static_zone()["ToxicCratePosition"]


def _with_nested(row: dict, **edits) -> dict:
    """*row* as the table sends it after the tree under it was edited."""
    base = {k: row["nested"][k] for k in edits}
    return {**row, "values": {**row["values"], "$nested": edits, "$nested_base": base}}


def test_a_static_zone_s_particles_crates_and_creatures_are_its_tree(tzc):
    row = _spec("toxiczone-static-zones").load(tzc)["rows"][0]
    assert row["nested"] == {k: _static_zone()[k] for k in ("ParticlesPosition", "ToxicCratePosition", "CreatureList")}
    particles = [{"NName": "Smoke", "PPos": [5000.0, 0.0, 6000.0]}, {"NName": "Smoke2", "PPos": ["5010", "0", "6010"]}]
    _spec("toxiczone-static-zones").save(tzc, [_with_nested(row, ParticlesPosition=particles)])
    zone = _doc(tzc, ops.STATIC)["StaticToxicAreaLocation"][0]
    assert zone["ParticlesPosition"][1] == {"NName": "Smoke2", "PPos": [5010.0, 0.0, 6010.0]}
    assert zone["ToxicCratePosition"] == _static_zone()["ToxicCratePosition"]
    assert zone["Radius"] == 300.0


def test_a_static_zone_tree_edit_over_a_changed_file_is_refused(tzc):
    row = _spec("toxiczone-static-zones").load(tzc)["rows"][0]
    _write(tzc, ops.STATIC, _static(_static_zone(crates=((1.0, 2.0, 3.0),))))
    with pytest.raises(ValueError, match="Plant: ToxicCratePosition changed in the file"):
        _spec("toxiczone-static-zones").save(tzc, [_with_nested(row, ToxicCratePosition=[])])


def test_the_clothes_table_edits_its_list_as_a_tree(tzc):
    slot = {"SlotName": "Mask", "ListProtections": [{"ClotheName": "GP5GasMask", "Protection": 1}]}
    _write(tzc, ops.CLOTHES, {"RequiredProtection": 5, "TZListSlotProtection": [slot]})
    body = _spec("toxiczone-clothes").load(tzc)
    assert [c["key"] for c in body["columns"]] == ["SlotName"]
    row = body["rows"][0]
    _spec("toxiczone-clothes").save(tzc, [_with_nested(row, ListProtections=[{"ClotheName": "GP5GasMask", "Protection": "2"}])])
    assert _doc(tzc, ops.CLOTHES)["TZListSlotProtection"][0]["ListProtections"] == [
        {"ClotheName": "GP5GasMask", "Protection": 2}
    ]


def test_a_new_static_zone_is_a_copy(tzc):
    rows = _spec("toxiczone-static-zones").load(tzc)["rows"]
    with pytest.raises(ValueError, match="duplicated"):
        _spec("toxiczone-static-zones").save(tzc, [*rows, {"id": "new-1", "values": {}}])


# ── a save writes back only what changed (plans/54 D follow-up) ────────


def _as_the_mod_writes_it(tzc) -> str:
    """The static file the way ToxicZone writes it: 17 significant digits,
    a float32 written out as a double, no exponent and no final newline."""
    from tests.test_toxic_zone import _static

    zone = _static_zone("TZTisy", x=1647.6500244140625, y=13000.0, radius=300.20001220703125,
                        crates=((1647.6500244140625, -3.000000106112566e-06, 13000.0),))
    text = json.dumps(_static(zone), indent=4)
    text = text.replace("1647.6500244140625", "1647.6500244140626")
    text = text.replace("-3.000000106112566e-06", "-0.000003000000106112566")
    (ops.config_dir(tzc) / ops.STATIC).write_text(text, encoding="utf-8")
    return text


def test_a_table_save_that_changes_nothing_leaves_the_file_as_it_was(tzc):
    """F75 on the Configs page: the table shows Radius as 300.2 (the float32
    DayZ reads) and sends it back, and the save rewrote the whole file."""
    text = _as_the_mod_writes_it(tzc)
    body = _spec("toxiczone-static-zones").load(tzc)
    assert body["rows"][0]["values"]["Radius"] == 300.2

    _spec("toxiczone-static-zones").save(tzc, body["rows"])

    assert (ops.config_dir(tzc) / ops.STATIC).read_text(encoding="utf-8") == text


def test_a_table_edit_changes_only_its_own_line(tzc):
    text = _as_the_mod_writes_it(tzc)
    body = _spec("toxiczone-static-zones").load(tzc)
    body["rows"][0]["values"]["Radius"] = "450"

    _spec("toxiczone-static-zones").save(tzc, body["rows"])

    after = (ops.config_dir(tzc) / ops.STATIC).read_text(encoding="utf-8")
    changed = [(a.strip(), b.strip()) for a, b in zip(text.splitlines(), after.splitlines()) if a != b]
    assert changed == [('"Radius": 300.20001220703125,', '"Radius": 450.0,')]


def test_a_duplicated_row_keeps_the_texts_it_was_copied_from(tzc):
    text = _as_the_mod_writes_it(tzc)
    rows = _spec("toxiczone-static-zones").load(tzc)["rows"]
    copy = {"id": "dup-0-a", "values": {**rows[0]["values"], "ToxicZoneStatut": "TZCopy"}}

    _spec("toxiczone-static-zones").save(tzc, [copy, *rows])

    after = (ops.config_dir(tzc) / ops.STATIC).read_text(encoding="utf-8")
    # The copy is a deep copy of the entry, so its positions keep their text too.
    assert after.count("1647.6500244140626") == 2 * text.count("1647.6500244140626")
    assert "e-06" not in after


# ── moving zones (D3, D7, D8) ─────────────────────────────────────────


def test_the_moving_table_hides_the_derived_values_and_shows_speed_unsigned(tzc):
    _write(tzc, ops.MOVING, _moving(_moving_zone(start=(3000.0, 1000.0), end=(1000.0, 2000.0), speed=-20)))
    body = _spec("toxiczone-moving-zones").load(tzc)
    keys = {c["key"] for c in body["columns"]}
    assert not keys & {"Coeff", "YtoOrigin", "Norme"}
    assert body["rows"][0]["values"]["Speed"] == 20


def test_a_moving_zone_save_computes_its_line_and_direction(tzc):
    body = _spec("toxiczone-moving-zones").load(tzc)
    values = body["rows"][0]["values"]
    values.update(StartX="3000", StartY="1000", EndX="1000", EndY="2000", Speed="25")
    _spec("toxiczone-moving-zones").save(tzc, body["rows"])
    zone = _doc(tzc, ops.MOVING)["MovingToxicAreaLocation"][0]
    assert zone["Coeff"] == pytest.approx(-0.5)
    assert zone["YtoOrigin"] == pytest.approx(2500.0)
    assert zone["Norme"] == 2000
    assert zone["Speed"] == -25  # west
    # And the checks agree with what the editor wrote.
    assert not [f for f in ops.build_report(tzc)["findings"] if f["id"].startswith("toxiczone_moving")]


def test_the_mods_stale_default_is_fixed_by_one_save(tzc):
    stale = _moving_zone("ZelenToNovy", start=(2000.0, 5000.0), end=(7442.0, 8330.0), speed=30,
                         Coeff=0.6119073629379272, YtoOrigin=5000.0, Norme=6379)
    _write(tzc, ops.MOVING, _moving(stale))
    rows = _spec("toxiczone-moving-zones").load(tzc)["rows"]
    _spec("toxiczone-moving-zones").save(tzc, rows)
    zone = _doc(tzc, ops.MOVING)["MovingToxicAreaLocation"][0]
    assert zone["YtoOrigin"] == pytest.approx(3776.185, abs=0.01)
    assert zone["Norme"] == 5442
    assert zone["Speed"] == 30


def test_a_north_south_moving_zone_is_refused_by_name(tzc):
    body = _spec("toxiczone-moving-zones").load(tzc)
    body["rows"][0]["values"].update(StartX="1000", EndX="1000")
    with pytest.raises(ValueError, match="Line: StartX and EndX are the same"):
        _spec("toxiczone-moving-zones").save(tzc, body["rows"])


# ── D4: replacing the author's AdminUID ───────────────────────────────


def test_replace_author_uid_swaps_only_the_authors_id(tzc):
    from app.web.editors.toxiczone import replace_author_uid

    _write(tzc, ops.MOVING, _moving(admins=(ops.AUTHOR_UID, "76561190000000002")))
    assert replace_author_uid(tzc, ADMIN) == [ADMIN, "76561190000000002"]
    with pytest.raises(ValueError, match="does not list"):
        replace_author_uid(tzc, ADMIN)
    _write(tzc, ops.MOVING, _moving(admins=(ops.AUTHOR_UID, ADMIN)))
    assert replace_author_uid(tzc, ADMIN) == [ADMIN]  # no duplicate
    with pytest.raises(ValueError, match="not a Steam64"):
        replace_author_uid(tzc, "12345")


def test_recompute_paths_fixes_only_what_is_wrong(tzc):
    from app.web.editors.toxiczone import recompute_paths

    stale = _moving_zone("ZelenToNovy", start=(2000.0, 5000.0), end=(7442.0, 8330.0), speed=30,
                         Coeff=0.6119073629379272, YtoOrigin=5000.0, Norme=6379)
    vertical = _moving_zone("Up", start=(1000.0, 1000.0), end=(1000.0, 3000.0))
    _write(tzc, ops.MOVING, _moving(stale, _moving_zone("Fine"), vertical))
    assert recompute_paths(tzc) == ["ZelenToNovy"]
    zones = _doc(tzc, ops.MOVING)["MovingToxicAreaLocation"]
    assert zones[0]["YtoOrigin"] == pytest.approx(3776.185, abs=0.01) and zones[0]["Norme"] == 5442
    assert zones[2] == vertical  # its own finding explains it; nothing to compute
    before = (ops.config_dir(tzc) / ops.MOVING).stat().st_mtime_ns
    assert recompute_paths(tzc) == []
    assert (ops.config_dir(tzc) / ops.MOVING).stat().st_mtime_ns == before  # no write


# ── through the API ───────────────────────────────────────────────────


@pytest.fixture()
def client(tzc):
    from app.web import server
    from app.web.auth import hash_password

    tzc.web_password_hash = hash_password(PASSWORD)
    c = TestClient(server.create_app(tzc))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_the_report_route(client, tzc):
    body = client.get("/api/toxiczone").json()
    assert body["installed"] is True and body["mod"] == MOD
    assert body["findings"] == []
    assert [z["name"] for z in body["zones"]] == ["Plant"]
    assert body["moving"][0]["start"] == [1000.0, 2000.0]
    write_test_mods(tzc, ['@CF'])
    assert client.get("/api/toxiczone").json() == {"installed": False}


def test_the_admin_uid_route(client, tzc):
    _write(tzc, ops.MOVING, _moving(admins=(ops.AUTHOR_UID,)))
    resp = client.post("/api/toxiczone/admin-uid", json={"steam_id": "nope"})
    assert resp.status_code == 400 and "Steam64" in resp.json()["detail"]
    resp = client.post("/api/toxiczone/admin-uid", json={"steam_id": ADMIN})
    assert resp.status_code == 200 and resp.json() == {"admin_uid": [ADMIN]}
    assert _doc(tzc, ops.MOVING)["AdminUID"] == [ADMIN]


def test_the_raw_tab_refuses_a_decimal_speed(client, tzc):
    doc = _moving(_moving_zone(speed=20))
    doc["MovingToxicAreaLocation"][0]["Speed"] = 20.5
    resp = client.put(
        "/api/configs/toxiczone-moving/raw", json={"target": ops.MOVING, "content": json.dumps(doc)}
    )
    assert resp.status_code == 400 and "Speed = 20.5" in resp.json()["detail"]


def test_the_moving_table_through_the_api(client, tzc):
    body = client.get("/api/configs/toxiczone-moving-zones").json()
    body["rows"][0]["values"]["EndX"] = str(body["rows"][0]["values"]["StartX"])
    resp = client.put("/api/configs/toxiczone-moving-zones", json={"rows": body["rows"]})
    assert resp.status_code == 400 and "StartX and EndX are the same" in resp.json()["detail"]


def test_the_recompute_route(client, tzc):
    _write(tzc, ops.MOVING, _moving(_moving_zone(start=(3000.0, 1000.0), end=(1000.0, 2000.0), speed=20)))
    resp = client.post("/api/toxiczone/recompute-paths")
    assert resp.status_code == 200 and resp.json() == {"fixed": ["Line"]}
    assert _doc(tzc, ops.MOVING)["MovingToxicAreaLocation"][0]["Speed"] == -20
    assert not [f for f in client.get("/api/toxiczone").json()["findings"] if f["id"].startswith("toxiczone_moving")]
