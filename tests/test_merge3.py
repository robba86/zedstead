"""The merge3 dispatcher: XML, JSON and text, plus quick tweaks (plans/71 Step 2)."""

from __future__ import annotations

import json

from app.shared.merge3 import BOTH_CHANGED, DAYZ_REMOVED, find_json, merge3, merge_text3

TYPES = '<types><type name="Rice"><nominal>10</nominal><min>6</min></type><type name="Can"><nominal>4</nominal></type></types>'
REL = "dayzOffline.chernarusplus/db/types.xml"


def _b(text):
    return text.encode()


def test_xml_goes_through_the_xml_merge():
    mine = TYPES.replace("<nominal>4</nominal>", "<nominal>2</nominal>")
    theirs = TYPES.replace("<min>6</min>", "<min>7</min>")
    result = merge3(REL, _b(TYPES), _b(mine), _b(theirs))
    assert result.fallback is None
    assert "<nominal>2</nominal>" in result.text and "<min>7</min>" in result.text
    assert (result.dayz, result.yours) == (1, 1)


def test_an_unmergeable_file_says_why():
    result = merge3(REL, _b(TYPES), _b(TYPES), _b("<types>"))
    assert result.text is None and "does not parse" in result.fallback


def test_a_tweaked_value_is_scaled_again_from_dayzs_new_value():
    """Food x0.5 wrote 5 over stock 10; DayZ raised it to 20: 20 x 0.5 = 10."""
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>5</nominal>")
    theirs = TYPES.replace("<nominal>10</nominal>", "<nominal>20</nominal>")
    sidecars = {".zsm-tweaks.json": {"Rice": {"nominal": {"base": 10, "written": 5}}}}
    result = merge3(REL, _b(TYPES), _b(mine), _b(theirs), sidecars=sidecars)
    assert "<nominal>10</nominal>" in result.text
    assert result.rows == [] and result.tweaks == 1
    assert result.sidecar_updates == {".zsm-tweaks.json": {"Rice": {"nominal": {"base": 20, "written": 10}}}}


def test_a_hand_edit_after_the_tweak_is_a_normal_conflict():
    mine = TYPES.replace("<nominal>10</nominal>", "<nominal>7</nominal>")
    theirs = TYPES.replace("<nominal>10</nominal>", "<nominal>20</nominal>")
    sidecars = {".zsm-tweaks.json": {"Rice": {"nominal": {"base": 10, "written": 5}}}}
    result = merge3(REL, _b(TYPES), _b(mine), _b(theirs), sidecars=sidecars)
    assert "<nominal>7</nominal>" in result.text and result.rows[0]["kind"] == BOTH_CHANGED
    assert result.sidecar_updates == {}


def test_tweaks_apply_only_to_the_files_the_sidecars_cover():
    rel = "dayzOffline.chernarusplus/cfgspawnabletypes.xml"
    base = '<spawnabletypes><type name="Rice"><nominal>10</nominal></type></spawnabletypes>'
    sidecars = {".zsm-tweaks.json": {"Rice": {"nominal": {"base": 10, "written": 5}}}}
    result = merge3(rel, _b(base), _b(base.replace(">10<", ">5<")), _b(base.replace(">10<", ">20<")), sidecars=sidecars)
    assert result.tweaks == 0 and len(result.rows) == 1


GAMEPLAY = {"version": 123, "GeneralData": {"disableBaseDamage": False, "disableContainerDamage": False},
            "WorldsData": {"lightingConfig": 0}}


def _json(doc):
    return json.dumps(doc).encode()


def test_json_merges_by_key_path():
    mine = json.loads(json.dumps(GAMEPLAY))
    mine["GeneralData"]["disableBaseDamage"] = True
    theirs = json.loads(json.dumps(GAMEPLAY))
    theirs["WorldsData"]["lightingConfig"] = 1
    theirs["WheelsData"] = {"newKey": 2}
    result = merge3("m/cfggameplay.json", _json(GAMEPLAY), _json(mine), _json(theirs))
    merged = json.loads(result.text)
    assert merged["GeneralData"]["disableBaseDamage"] is True
    assert merged["WorldsData"]["lightingConfig"] == 1 and merged["WheelsData"] == {"newKey": 2}


def test_json_value_both_changed_is_listed_with_its_path():
    mine = json.loads(json.dumps(GAMEPLAY))
    mine["WorldsData"]["lightingConfig"] = 2
    theirs = json.loads(json.dumps(GAMEPLAY))
    theirs["WorldsData"]["lightingConfig"] = 1
    result = merge3("m/cfggameplay.json", _json(GAMEPLAY), _json(mine), _json(theirs))
    (row,) = result.rows
    assert (row["entry"], row["field"], row["mine"], row["theirs"], row["live"]) == ("WorldsData", "lightingConfig", "2", "1", "mine")
    parent, slot = find_json(json.loads(result.text), row["keys"])
    assert parent[slot] == 2


def test_json_areas_are_matched_by_name():
    base = {"Areas": [{"AreaName": "A", "Radius": 100}, {"AreaName": "B", "Radius": 50}]}
    mine = {"Areas": [{"AreaName": "A", "Radius": 150}, {"AreaName": "B", "Radius": 50}]}
    theirs = {"Areas": [{"AreaName": "B", "Radius": 60}, {"AreaName": "A", "Radius": 100}, {"AreaName": "C", "Radius": 10}]}
    merged = json.loads(merge3("m/cfgeffectarea.json", _json(base), _json(mine), _json(theirs)).text)
    assert merged["Areas"] == [{"AreaName": "B", "Radius": 60}, {"AreaName": "A", "Radius": 150}, {"AreaName": "C", "Radius": 10}]


def test_json_key_dayz_removed_goes():
    base = {"a": 1, "b": 2}
    result = merge3("m/x.json", _json(base), _json({"a": 1, "b": 3}), _json({"a": 1}))
    assert json.loads(result.text) == {"a": 1} and result.rows[0]["kind"] == DAYZ_REMOVED


INIT = "void main()\n{\n\tint hour = 8;\n\tint minute = 0;\n\tGetGame().SetDate(1, 9, hour, minute);\n}\n"


def test_text_edits_in_different_places_both_come_in():
    mine = INIT.replace("hour = 8", "hour = 12")
    theirs = INIT.replace("SetDate(1, 9", "SetDate(2, 10")
    assert merge_text3(INIT, mine, theirs) == INIT.replace("hour = 8", "hour = 12").replace("SetDate(1, 9", "SetDate(2, 10")


def test_text_edits_that_touch_fall_back():
    mine = INIT.replace("hour = 8", "hour = 12")
    theirs = INIT.replace("minute = 0", "minute = 30")
    result = merge3("m/init.c", _b(INIT), _b(mine), _b(theirs))
    assert result.text is None and result.fallback == "overlapping edits"


def test_the_same_text_edit_on_both_sides_is_taken_once():
    mine = INIT.replace("hour = 8", "hour = 12")
    assert merge3("m/init.c", _b(INIT), _b(mine), _b(mine)).text == mine
