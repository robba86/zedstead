"""Tests for /api/types* (app/web/routers/types.py)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CE_1_29, bohemia_ce, ce_lines, ce_pick
from tests.test_limitsdefinition_xml import limits_user

PASSWORD = "testpass123"

TYPES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="AK74">
        <nominal>5</nominal>
        <lifetime>3600</lifetime>
        <restock>0</restock>
        <min>2</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0"/>
        <category name="weapons"/>
        <usage name="Military"/>
        <value name="Tier2"/>
    </type>
    <type name="Apple">
        <nominal>10</nominal>
        <lifetime>7200</lifetime>
        <restock>0</restock>
        <min>5</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <category name="food"/>
        <usage name="Farm"/>
        <value name="Tier1"/>
    </type>
</types>
"""


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def types_file(sample_config):
    db = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus" / "db"
    db.mkdir(parents=True, exist_ok=True)
    path = db / "types.xml"
    path.write_text(TYPES_XML)
    return path


def test_types_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/types/files")
    assert resp.status_code == 401


def test_list_files(client, types_file):
    resp = client.get("/api/types/files")
    assert resp.status_code == 200
    assert any(str(types_file) == f for f in resp.json()["files"])


def test_list_types(client, types_file):
    resp = client.get("/api/types", params={"file": str(types_file)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = {e["name"] for e in body["entries"]}
    assert names == {"AK74", "Apple"}


def test_list_types_search_and_category(client, types_file):
    resp = client.get("/api/types", params={"file": str(types_file), "q": "ak"})
    assert resp.json()["total"] == 1

    resp = client.get("/api/types", params={"file": str(types_file), "category": "food"})
    body = resp.json()
    assert body["total"] == 1
    assert body["entries"][0]["name"] == "Apple"


def test_list_types_smart_filters(client, types_file):
    """The Smart view filters by usage, value tier and a rarity band's nominal range."""
    def names(**params):
        body = client.get("/api/types", params={"file": str(types_file), **params}).json()
        return {e["name"] for e in body["entries"]}

    assert names(usage="Military") == {"AK74"}
    assert names(value="Tier1") == {"Apple"}
    assert names(nominal_min=4, nominal_max=10) == {"AK74", "Apple"}
    assert names(nominal_min=6) == {"Apple"}
    assert names(nominal_max=0) == set()


def test_the_multiplier_writes_only_the_rows_the_smart_filters_show(client, types_file):
    resp = client.post(
        "/api/types/multiplier",
        json={"file": str(types_file), "usage": "Farm", "expected": 1, "nominal": 2.0},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    entries = {e["name"]: e["nominal"] for e in client.get("/api/types", params={"file": str(types_file)}).json()["entries"]}
    assert entries == {"AK74": 5, "Apple": 20}


def test_the_filler_writes_only_where_a_value_is_0(client, types_file):
    """A multiplier cannot move a 0, so the Filler gives the filtered rows a
    number where they have 0 and leaves every other number alone."""
    resp0 = client.put("/api/types/entry", json={"source_file": str(types_file), "name": "AK74", "nominal": 0, "min": 0})
    assert resp0.status_code == 200, resp0.text
    resp = client.post(
        "/api/types/fill",
        json={"file": str(types_file), "expected": 2, "nominal": 8, "min": 3},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 2
    rows = {e["name"]: (e["nominal"], e["min"]) for e in client.get("/api/types", params={"file": str(types_file)}).json()["entries"]}
    assert rows["AK74"] == (8, 3)
    assert rows["Apple"][0] == 10


def test_a_fill_that_puts_min_above_nominal_is_refused(client, types_file):
    resp0 = client.put("/api/types/entry", json={"source_file": str(types_file), "name": "AK74", "min": 0})
    assert resp0.status_code == 200, resp0.text
    before = types_file.read_bytes()
    resp = client.post("/api/types/fill", json={"file": str(types_file), "q": "AK74", "min": 50})
    assert resp.status_code == 400
    assert "min above nominal for 1 entries (AK74)" in resp.json()["detail"]
    assert types_file.read_bytes() == before


def test_a_fill_with_no_value_is_refused(client, types_file):
    resp = client.post("/api/types/fill", json={"file": str(types_file)})
    assert resp.status_code == 400


def test_list_types_pagination(client, types_file):
    resp = client.get("/api/types", params={"file": str(types_file), "offset": 1, "limit": 1})
    body = resp.json()
    assert body["total"] == 2
    assert len(body["entries"]) == 1


def test_item_categories_name_every_item_by_its_icon_category(client, types_file):
    """The lookup behind the item icons: lowercased name to analyzer category."""
    resp = client.get("/api/types/categories")
    assert resp.status_code == 200
    assert resp.json()["categories"] == {"ak74": "weapons", "apple": "apple"}


def test_medicine_filed_under_tools_still_gets_the_medical_icon(client, types_file):
    """Stock types.xml files Morphine under tools: the name rule wins for the icon."""
    types_file.write_text(TYPES_XML.replace('name="Apple"', 'name="Morphine"').replace('"food"', '"tools"'))
    assert client.get("/api/types/categories").json()["categories"]["morphine"] == "medical"


@pytest.mark.parametrize("name, group", [
    ("ZmbM_HermitSkinny_Base", "infected"),
    ("Animal_CervusElaphus", "animals"),
    ("Land_Wreck_V3S_DE", "world"),
    ("Boat_01_Black", "boats"),
    ("StaticObj_Wreck_Train_742_Red_DE", "train"),
    ("Land_Train_Wagon_Box_DE", "train"),
    ("Land_Wreck_sed01_aban1_black_DE", "car"),
    ("Land_Wreck_Trailer_Closed_DE", "world"),
    ("Fence", "fence"),
    ("Cauldron", "other"),
])
def test_an_item_with_no_category_gets_an_icon_group_from_its_name(name, group):
    """The stock types.xml has 281 uncategorised items: infected, animals, map objects."""
    from app.ops.economy_analyzer import icon_category
    from app.xml.types_xml import TypeEntry

    entry = TypeEntry(name, 0, 0, 0, 0, -1, -1, 0, category="", tag="")
    assert icon_category(entry) == group


@pytest.mark.parametrize("name, category, kind", [
    ("BoneKnife", "tools", "bone"),
    ("HuntingKnife", "tools", "knife"),
    ("Binoculars", "tools", "binoculars"),
    ("Mag_AKM_30Rnd", "weapons", "magazine"),
    ("Ammo_12gaSlug", "weapons", "ammo"),
    ("AmmoBox_00buck_10rnd", "weapons", "ammobox"),
    ("AK_WoodBttstck", "weapons", "buttstock"),
    ("Colt1911", "weapons", "pistol"),
    ("PistolOptic", "weapons", "optic"),
    ("PSO1Optic", "weapons", "optic"),
    ("Glock19", "weapons", "pistol"),
    ("Mag_Glock_15Rnd", "weapons", "magazine"),
    ("AmmoBox", "containers", "ammobox"),
    ("Bear_Pink", "containers", "teddy"),
    ("BearTrap", "tools", "tools"),
    ("NBCGloves", "clothes", "gloves"),
    ("AthleticShoes_Black", "clothes", "shoes"),
    ("CargoPants_Blue", "clothes", "pants"),
    ("GasMask", "clothes", "mask"),
    ("AviatorGlasses", "clothes", "glasses"),
    ("Armband_Red", "clothes", "armband"),
    ("BoonieHat_Tan", "clothes", "hat"),
    ("BaseballCap_Red", "clothes", "cap"),
    ("PoliceCap", "clothes", "cap"),
    ("HighCapacityVest_Black", "clothes", "clothing"),
    ("HatchbackHood", "lootdispatch", "lootdispatch"),
    ("BoletusMushroom", "food", "mushroom"),
    ("Apple", "food", "apple"),
    ("AKM", "weapons", "weapons"),
    # TraderPlus's items, in whatever category the mod's types file gives them.
    ("TraderPlus_Money_Dollar100", "tools", "money"),
    ("TraderPlus_Wallet", "tools", "wallet"),
    ("CarLockpick", "tools", "lockpick"),
    ("TraderPlusVehicleCargo", "tools", "cargo"),
    ("TraderPlus_VehicleReceipt", "tools", "receipt"),
])
def test_a_name_keyword_picks_a_finer_icon_inside_its_category(name, category, kind):
    """economy_analyzer._ICON_KINDS: the keyword counts only inside its own category."""
    from app.ops.economy_analyzer import icon_category
    from app.xml.types_xml import TypeEntry

    entry = TypeEntry(name, 0, 0, 0, 0, -1, -1, 0, category=category, tag="")
    assert icon_category(entry) == kind


def test_types_meta(client, types_file):
    resp = client.get("/api/types/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert "weapons" in body["categories"]
    assert "food" in body["categories"]
    assert "flags" in body


def test_update_entry(client, types_file):
    resp = client.put(
        "/api/types/entry",
        json={"source_file": str(types_file), "name": "AK74", "nominal": 8, "category": "weapons"},
    )
    assert resp.status_code == 200
    assert resp.json()["nominal"] == 8

    resp = client.get("/api/types", params={"file": str(types_file), "q": "ak74"})
    assert resp.json()["entries"][0]["nominal"] == 8


@pytest.mark.parametrize("change, message", [
    ({"min": 20}, "min (20) cannot be above nominal (5)"),
    ({"nominal": 1}, "min (2) cannot be above nominal (1)"),
    ({"lifetime": -100}, "lifetime must be at least 0"),
    ({"quantmax": -2}, "quantmax must be at least -1"),
    # A fill percentage (types_xml.QUANT_MAX); stock values stay inside 0..100.
    ({"quantmax": 150}, "quantmax is a percentage: -1 or 0 to 100, not 150"),
    ({"quantmin": 60, "quantmax": 40}, "quantmin (60) cannot be above quantmax (40)"),
])
def test_update_entry_refuses_numbers_the_economy_cannot_use(client, types_file, change, message):
    """F30: min above nominal and a negative lifetime were written as is."""
    before = types_file.read_bytes()
    resp = client.put("/api/types/entry", json={"source_file": str(types_file), "name": "AK74", **change})
    assert resp.status_code == 400
    assert resp.json()["detail"] == message
    assert types_file.read_bytes() == before
    # Nothing reached the cache either.
    row = client.get("/api/types", params={"file": str(types_file), "q": "ak74"}).json()["entries"][0]
    assert (row["nominal"], row["min"], row["lifetime"]) == (5, 2, 3600)


def test_update_entry_keeps_minus_one_for_quantities(client, types_file):
    resp = client.put("/api/types/entry", json={
        "source_file": str(types_file), "name": "AK74", "quantmin": -1, "quantmax": -1, "min": 5,
    })
    assert resp.status_code == 200, resp.text


# A mod's override in custom/ names only what it changes
# (central-economy-mission-files-modding.wiki:38), and Bohemia's own types.xml
# has 805 types with nominal 0 and min 1.
OVERRIDE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="Apple">
        <nominal>5</nominal>
        <min>2</min>
        <category name="food"/>
    </type>
    <type name="Rag">
        <nominal>0</nominal>
        <lifetime>14400</lifetime>
        <restock>0</restock>
        <min>1</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <category name="tools"/>
    </type>
</types>
"""


@pytest.fixture()
def override_file(types_file):
    custom = types_file.parent.parent / "custom"
    custom.mkdir()
    path = custom / "mod-types.xml"
    path.write_text(OVERRIDE_XML)
    return path


def test_a_save_into_custom_says_the_next_merge_undoes_it(client, override_file, types_file):
    """An edit to a mod's custom/ copy was undone by the
    next merge without a word. It still saves, and says so; db/types.xml
    carries no warning."""
    resp = client.put("/api/types/entry", json={
        "source_file": str(override_file), "name": "Apple", "nominal": 6,
    })
    assert resp.status_code == 200, resp.text
    assert "Every merge" in resp.json()["warning"]
    resp = client.post("/api/types/multiplier", json={"file": str(override_file), "nominal": 2.0})
    assert "Every merge" in resp.json()["warning"]
    resp = client.put("/api/types/entry", json={
        "source_file": str(types_file), "name": "Apple", "nominal": 11,
    })
    assert "warning" not in resp.json()


def test_an_edit_writes_only_what_it_changes(client, override_file):
    resp = client.put("/api/types/entry", json={
        "source_file": str(override_file), "name": "Apple", "category": "tools",
    })
    assert resp.status_code == 200, resp.text
    apple = override_file.read_text().split("</type>")[0]
    assert "<lifetime>" not in apple and "<restock>" not in apple
    # A value sent back as it is writes nothing either.
    resp = client.put("/api/types/entry", json={
        "source_file": str(override_file), "name": "Apple", "lifetime": 0, "restock": 0,
    })
    assert resp.status_code == 200, resp.text
    assert "<lifetime>" not in override_file.read_text().split("</type>")[0]


def test_a_stock_nominal_zero_type_still_saves(client, override_file):
    resp = client.put("/api/types/entry", json={
        "source_file": str(override_file), "name": "Rag", "category": "clothes",
    })
    assert resp.status_code == 200, resp.text
    resp = client.put("/api/types/entry", json={
        "source_file": str(override_file), "name": "Rag", "lifetime": 100,
    })
    assert resp.status_code == 200, resp.text


def test_a_new_child_goes_in_bohemias_order(client, override_file):
    resp = client.put("/api/types/entry", json={
        "source_file": str(override_file), "name": "Apple", "lifetime": 3600,
    })
    assert resp.status_code == 200, resp.text
    apple = override_file.read_text().split("</type>")[0]
    assert apple.index("<nominal>") < apple.index("<lifetime>") < apple.index("<min>")


def test_a_multiplier_that_puts_min_above_nominal_is_refused(client, types_file):
    before = types_file.read_bytes()
    resp = client.post("/api/types/multiplier", json={"file": str(types_file), "nominal": 0.3})
    assert resp.status_code == 400
    assert "min above nominal for 2 entries (AK74, Apple)" in resp.json()["detail"]
    assert types_file.read_bytes() == before


def test_a_refused_edit_never_reaches_the_file_through_another_save(sample_config, types_file, monkeypatch):
    """The parsed file is one cached tree that an edit changes in place before
    its last check. Another edit saving meanwhile wrote the first one's
    half-applied change to disk, though that edit was then refused."""
    import threading

    from app.web import server
    from app.web.auth import hash_password
    from app.web.routers import types as types_router

    entered, release = threading.Event(), threading.Event()

    def _problem(name, *_args):
        if name != "AK74":
            return None
        entered.set()
        release.wait(5)
        return "refused for the test"

    monkeypatch.setattr(types_router, "undefined_names_problem", _problem)
    sample_config.web_password_hash = hash_password(PASSWORD)
    results: dict[str, int] = {}

    with TestClient(server.create_app(sample_config)) as tclient:
        tclient.post("/api/auth/login", json={"password": PASSWORD})

        def _put(name: str, change: dict) -> None:
            resp = tclient.put("/api/types/entry", json={"source_file": str(types_file), "name": name, **change})
            results[name] = resp.status_code

        refused = threading.Thread(target=_put, args=("AK74", {"category": "tools"}))
        refused.start()
        assert entered.wait(5)
        saved = threading.Thread(target=_put, args=("Apple", {"nominal": 12}))
        saved.start()
        saved.join(0.5)
        release.set()
        refused.join(5)
        saved.join(5)

    assert results == {"AK74": 400, "Apple": 200}
    text = types_file.read_text()
    assert "<nominal>12</nominal>" in text
    assert 'name="tools"' not in text


def test_update_unknown_entry_400(client, types_file):
    resp = client.put(
        "/api/types/entry", json={"source_file": str(types_file), "name": "DoesNotExist", "nominal": 1}
    )
    assert resp.status_code == 400


def test_multiplier_preview(client, types_file):
    resp = client.get(
        "/api/types/multiplier/preview", params={"file": str(types_file), "nominal": 2.0}
    )
    assert resp.status_code == 200
    changes = {c["name"]: c["nominal"] for c in resp.json()["changes"]}
    assert changes["AK74"] == [5, 10]


def test_apply_multiplier(client, types_file):
    resp = client.post(
        "/api/types/multiplier",
        json={"file": str(types_file), "category": "weapons", "nominal": 2.0},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1

    resp = client.get("/api/types", params={"file": str(types_file), "q": "ak74"})
    assert resp.json()["entries"][0]["nominal"] == 10


def test_the_multiplier_writes_only_the_rows_the_search_shows(client, types_file):
    """The dialog counted the 18 rows the name search
    showed, and the save multiplied all 2382 in the file."""
    shown = client.get("/api/types", params={"file": str(types_file), "q": "ak"}).json()["total"]
    assert shown == 1
    resp = client.post(
        "/api/types/multiplier",
        json={"file": str(types_file), "q": "ak", "expected": shown, "nominal": 2.0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1
    rows = {e["name"]: e["nominal"] for e in client.get(
        "/api/types", params={"file": str(types_file)}
    ).json()["entries"]}
    assert rows["AK74"] == 10
    assert rows["Apple"] == 10  # untouched


def test_the_multiplier_preview_reads_the_search_too(client, types_file):
    resp = client.get(
        "/api/types/multiplier/preview",
        params={"file": str(types_file), "q": "ak", "nominal": 2.0},
    )
    assert [c["name"] for c in resp.json()["changes"]] == ["AK74"]


def test_a_multiplier_whose_count_no_longer_matches_is_refused(client, types_file):
    before = types_file.read_bytes()
    resp = client.post(
        "/api/types/multiplier", json={"file": str(types_file), "expected": 1, "nominal": 2.0}
    )
    assert resp.status_code == 400
    assert "matches 2 entries, not the 1" in resp.json()["detail"]
    assert types_file.read_bytes() == before


def test_restore_types_file(client, types_file, monkeypatch):
    monkeypatch.setattr("app.web.routers.types.restore_types_file", lambda path, shipped=None: True)
    resp = client.post("/api/types/restore", json={"file": str(types_file)})
    assert resp.status_code == 200
    assert resp.json()["restored"] is True


def test_a_types_file_outside_the_mission_is_refused(client, types_file, tmp_path, monkeypatch):
    """`file` is matched against `scan_types_files`, never opened as given —
    a real types.xml elsewhere on the box is not one of them (plans/57 E1)."""
    outside = tmp_path / "outside" / "types.xml"
    outside.parent.mkdir()
    outside.write_text(TYPES_XML)
    restored: list = []
    monkeypatch.setattr(
        "app.web.routers.types.restore_types_file", lambda path, shipped=None: restored.append(path) or True
    )

    assert client.post("/api/types/restore", json={"file": str(outside)}).status_code == 400
    assert client.get("/api/types", params={"file": str(outside)}).json()["entries"] == []
    assert restored == []


# --- bounds and the cache guard (test pass 2, N28) --------------------------

@pytest.mark.parametrize("field,value", [("nominal", 0), ("lifetime", -1), ("min", 11), ("restock", 1e308)])
def test_multiplier_out_of_range_is_refused(client, types_file, field, value):
    before = types_file.read_bytes()
    resp = client.post("/api/types/multiplier", json={"file": str(types_file), field: value})
    assert resp.status_code == 422
    assert field in resp.json()["detail"]
    assert types_file.read_bytes() == before


def test_multiplier_preview_out_of_range_is_422_not_500(client, types_file):
    resp = client.get(
        "/api/types/multiplier/preview", params={"file": str(types_file), "lifetime": 0}
    )
    assert resp.status_code == 422


def test_a_multiplier_that_fails_half_way_leaves_nothing_in_the_cache(
    client, types_file, monkeypatch
):
    """The finding's sequence: the multiplier fails after editing the first
    entry, then an unrelated single-row save must not write that edit."""
    from app.web.routers import types as types_router

    real = types_router.write_multiplier
    calls = {"n": 0}

    def fail_on_second(entry, mult):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OverflowError("cannot convert float infinity to integer")
        real(entry, mult)

    monkeypatch.setattr(types_router, "write_multiplier", fail_on_second)
    resp = client.post("/api/types/multiplier", json={"file": str(types_file), "nominal": 2.0})
    assert resp.status_code == 400
    assert calls["n"] == 2

    resp = client.put(
        "/api/types/entry",
        json={"source_file": str(types_file), "name": "Apple", "nominal": 10},
    )
    assert resp.status_code == 200
    assert "<nominal>5</nominal>" in types_file.read_text()  # AK74, not doubled


def test_an_edit_keeps_the_file_s_comments_and_merge_markers(client, types_file):
    text = types_file.read_text()
    types_file.write_text(
        text.replace("<types>", "<types>\n    <!-- raised for the weekend event -->\n    <!-- START MOD: @user:extra -->", 1).replace("</types>", "    <!-- END MOD: @user:extra -->\n</types>", 1)
    )

    resp = client.put("/api/types/entry", json={"source_file": str(types_file), "name": "AK74", "nominal": 8})
    assert resp.status_code == 200, resp.text

    content = types_file.read_text()
    assert "<nominal>8</nominal>" in content
    for comment in ("<!-- raised for the weekend event -->", "<!-- START MOD: @user:extra -->", "<!-- END MOD: @user:extra -->"):
        assert comment in content


# ---------------------------------------------------------------------------
# The row's tree (plans/76 step 3): usage, value, tag and flags
# ---------------------------------------------------------------------------

MARKED_TYPES_XML = TYPES_XML.replace(
    '    <type name="Apple">',
    '    <!-- START MOD: @Food -->\n    <type name="Pear">\n        <nominal>1</nominal>\n    </type>\n'
    '    <!-- END MOD: @Food -->\n    <!-- DISABLED @Old: <type name="Plum"/> -->\n    <type name="Apple">',
)


def _ak74(client, types_file):
    return client.get("/api/types", params={"file": str(types_file), "q": "ak74"}).json()["entries"][0]


def test_a_type_entry_carries_its_tree_and_the_meta_its_choices(client, types_file):
    assert _ak74(client, types_file)["nested"] == {
        "usage": ["Military"],
        "value": ["Tier2"],
        "tag": [],
        "flags": {
            "count_in_cargo": 0, "count_in_hoarder": 0, "count_in_map": 1,
            "count_in_player": 0, "crafted": 0, "deloot": 0,
        },
    }
    meta = client.get("/api/types/meta").json()
    assert "Military" in meta["usages"]
    assert "tags" in meta
    assert meta["nested_switches"] == ["flags"]


def test_a_tree_edit_writes_in_place_and_keeps_the_rest_of_the_file(client, types_file):
    from app.xml.xml_utils import load_xml, save_xml

    types_file.write_text(MARKED_TYPES_XML)
    save_xml(load_xml(types_file, preserve_comments=True), types_file, snapshot=False)
    before = types_file.read_text()

    row = _ak74(client, types_file)
    tree = {"usage": ["Military", "Police"], "tag": ["shelves"], "flags": dict(row["nested"]["flags"], crafted=1)}
    resp = client.put("/api/types/entry", json={
        "source_file": str(types_file), "name": "AK74",
        "nested": tree, "nested_base": {k: row["nested"][k] for k in tree},
    })
    assert resp.status_code == 200, resp.text

    after = types_file.read_text()
    old_ak, new_ak = (re.search(r'<type name="AK74">.*?</type>', t, re.S).group() for t in (before, after))
    assert after.replace(new_ak, "") == before.replace(old_ak, "")
    assert "<!-- START MOD: @Food -->" in after and "<!-- DISABLED @Old:" in after
    assert re.findall(r"<(?:usage|value|tag|category) [^>]*>", new_ak) == [
        '<category name="weapons"/>',
        '<tag name="shelves"/>',
        '<usage name="Military"/>',
        '<usage name="Police"/>',
        '<value name="Tier2"/>',
    ]
    assert 'crafted="1"' in new_ak
    assert _ak74(client, types_file)["nested"]["usage"] == ["Military", "Police"]


def test_a_stale_tree_is_refused_and_leaves_file_and_cache_alone(client, types_file):
    row = _ak74(client, types_file)
    types_file.write_text(types_file.read_text().replace('<usage name="Military"/>', '<usage name="Police"/>'))
    before = types_file.read_bytes()

    resp = client.put("/api/types/entry", json={
        "source_file": str(types_file), "name": "AK74", "nominal": 4,
        "nested": {"usage": []}, "nested_base": {"usage": row["nested"]["usage"]},
    })
    assert resp.status_code == 400
    assert "usage changed in the file" in resp.json()["detail"]
    assert types_file.read_bytes() == before
    assert _ak74(client, types_file)["nominal"] == 5


# Livonia's stock cfglimitsdefinition.xml (dayzOffline.enoch), read at the
# pinned 1.29 commit, its usage list cut to three. It has no Tier4 and no
# vehiclesparts.
def livonia_limits_xml() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.enoch/cfglimitsdefinition.xml"))
    start, end = lines.index("    <usageflags>\n"), lines.index("    </usageflags>\n")
    usage = ce_pick(lines, "<usageflags>", 'name="Military"', 'name="Farm"', 'name="Town"', "</usageflags>")
    return "".join(lines[:start] + usage + lines[end + 1:])


@pytest.fixture()
def livonia_limits(types_file):
    mission = types_file.parent.parent
    (mission / "cfglimitsdefinition.xml").write_text(livonia_limits_xml())
    (mission / "cfglimitsdefinitionuser.xml").write_text(limits_user())
    return mission


def test_the_choices_are_the_missions_own_limits(client, livonia_limits):
    meta = client.get("/api/types/meta").json()

    assert "Tier4" not in meta["values"] and "Unique" in meta["values"]
    assert "vehiclesparts" not in meta["categories"] and "lootdispatch" in meta["categories"]
    assert meta["tags"] == ["floor", "ground", "shelves"]


def test_without_a_limits_file_the_choices_fall_back_to_stock_names(client, types_file):
    from app.xml.types_xml import KNOWN_CATEGORIES

    meta = client.get("/api/types/meta").json()

    assert "vehiclesparts" not in KNOWN_CATEGORIES
    assert set(KNOWN_CATEGORIES) <= set(meta["categories"]) and "Tier4" in meta["values"]


@pytest.mark.parametrize("change, named", [
    ({"category": "vehiclesparts"}, "category 'vehiclesparts'"),
    ({"values": ["Tier4"]}, "value 'Tier4'"),
    ({"nested": {"usage": ["Military", "Moon"]}, "nested_base": {"usage": ["Military"]}}, "usage 'Moon'"),
])
def test_a_name_the_mission_does_not_define_is_refused(client, types_file, livonia_limits, change, named):
    before = types_file.read_bytes()

    resp = client.put("/api/types/entry", json={"source_file": str(types_file), "name": "AK74", **change})

    assert resp.status_code == 400
    assert named in resp.json()["detail"] and "cfglimitsdefinition.xml" in resp.json()["detail"]
    assert types_file.read_bytes() == before


def test_a_defined_name_or_a_user_group_name_saves(client, types_file, livonia_limits):
    resp = client.put("/api/types/entry", json={
        "source_file": str(types_file), "name": "AK74", "category": "lootdispatch", "values": ["Tier12"],
    })
    assert resp.status_code == 200, resp.text


def test_a_name_already_in_the_file_does_not_stop_another_edit(client, types_file, livonia_limits):
    types_file.write_text(TYPES_XML.replace('<value name="Tier2"/>', '<value name="Tier4"/>'))

    resp = client.put("/api/types/entry", json={"source_file": str(types_file), "name": "AK74", "nominal": 6})
    assert resp.status_code == 200, resp.text


def test_the_custom_types_table_warns_and_refuses_an_undefined_name(override_file, livonia_limits):
    from app.web.editors.ce_tables import types_load, types_save

    override_file.write_text(OVERRIDE_XML.replace("</type>", '<category name="vehiclesparts"/></type>', 1))
    data = types_load(override_file)
    assert any("category 'vehiclesparts'" in w for w in data["warnings"])
    assert "Tier4" not in data["nested_choices"]["value"]

    types_save(override_file, data["rows"])  # untouched: saves
    row = {**data["rows"][1], "values": {**data["rows"][1]["values"], "tag": "attic"}}
    with pytest.raises(ValueError, match="tag 'attic'"):
        types_save(override_file, [row])


def test_replacing_usages_keeps_a_user_group_reference(types_file):
    from app.xml.types_xml import parse_types_file, set_usages

    types_file.write_text(TYPES_XML.replace(
        '<usage name="Military"/>', '<usage name="Military"/><usage user="TownVillage"/>'
    ))
    entry = parse_types_file(types_file).entries[0]

    set_usages(entry, ["Farm"])

    assert {(u.get("name"), u.get("user")) for u in entry.element.findall("usage")} == {
        (None, "TownVillage"), ("Farm", None),
    }


def test_list_types_facets_grey_out_choices_with_no_rows(client, types_file):
    """Each filter group lists the choices that still leave rows with the other
    filters on, and drops only its own filter, as a web shop's facets do."""
    def facets(**params):
        return client.get("/api/types", params={"file": str(types_file), **params}).json()["facets"]

    everything = facets()
    assert everything["categories"] == ["food", "weapons"]
    assert everything["usages"] == ["Farm", "Military"]
    assert everything["nominals"] == [5, 10]

    weapons = facets(category="weapons")
    assert weapons["categories"] == ["food", "weapons"]
    assert weapons["usages"] == ["Military"]
    assert weapons["values"] == ["Tier2"]
    assert weapons["nominals"] == [5]

    farm = facets(usage="Farm", nominal_min=6)
    assert farm["categories"] == ["food"]
    assert farm["usages"] == ["Farm"]
    assert farm["nominals"] == [10]
