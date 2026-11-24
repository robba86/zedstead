"""The Expansion settings editors (app/web/editors/expansion.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.web import editors
from app.web.editors import expansion as exp

FLAT = {
    "m_Version": 9,
    "Enabled": 1,
    "Multiplier": 2.5,
    "Label": "hello",
    "Whitelist": ["Expansion_C4_Explosion", "Expansion_RPG_Explosion"],
}

NESTED = {
    "m_Version": 32,
    "Enabled": 1,
    "Patrols": [{"Name": "one", "Positions": [[1, 2, 3]]}],
    "LoadBalancingCategories": {"a": 1},
}


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return path


def _profile_file(config, filename: str) -> Path:
    return config.server_dir_path.joinpath(config.profiles_path, *exp.PROFILE_DIR, filename)


def _mission_file(config, filename: str) -> Path:
    return config.server_dir_path.joinpath(
        "mpmissions", "dayzOffline.chernarusplus", *exp.MISSION_DIR, filename
    )


def test_every_file_expansion_writes_has_an_editor():
    # 21 in the profile folder + 9 in the mission folder, measured on a real
    # boot (plans/36). A file gained upstream needs a row here, not a new page.
    assert len(exp.SETTINGS) == 30
    assert sum(1 for e in exp.SETTINGS if e.where == "profile") == 21
    assert sum(1 for e in exp.SETTINGS if e.where == "mission") == 9
    ids = [e.id for e in exp.SETTINGS]
    assert len(set(ids)) == len(ids)
    assert all(e.id.startswith("expansion-") for e in exp.SETTINGS)
    assert all(e.filename.endswith("Settings.json") for e in exp.SETTINGS)


def test_every_entry_is_registered_in_the_editor_registry():
    # The 30 files, plus the patrol table over one of them (plans/64 D1).
    registered = {s.id: s for s in editors.list_editors() if s.group == "Expansion"}
    assert set(registered) == {e.id for e in exp.SETTINGS} | {"expansion-ai-patrol-list"}
    files = [s for s in registered.values() if s.id != "expansion-ai-patrol-list"]
    assert all(s.kind == "form" for s in files)
    assert registered["expansion-ai-patrol-list"].kind == "table"
    assert all(s.raw_targets is not None for s in registered.values())


def test_nothing_exists_until_the_server_has_booted(sample_config):
    # The whole point of per-file `exists`: a fresh install has none of them.
    for entry in exp.SETTINGS:
        spec = editors.get_editor(entry.id)
        assert spec.exists(sample_config) is False, entry.id


def test_a_profile_file_resolves_under_the_configured_profiles_path(sample_config):
    sample_config.profiles_path = "myprofiles"
    entry = next(e for e in exp.SETTINGS if e.id == "expansion-core")
    path = exp.settings_path(sample_config, entry)
    assert path == sample_config.server_dir_path / "myprofiles" / "ExpansionMod" / "Settings" / (
        "CoreSettings.json"
    )


def test_a_mission_file_needs_a_mission(sample_config):
    entry = next(e for e in exp.SETTINGS if e.id == "expansion-safezone")
    assert exp.settings_path(sample_config, entry) is not None
    sample_config.server_cfg_path.write_text('hostname = "x";\n')
    assert exp.settings_path(sample_config, entry) is None


def test_form_shows_scalars_and_names_what_it_hides(sample_config):
    _write(_profile_file(sample_config, "RaidSettings.json"), FLAT)
    spec = editors.get_editor("expansion-raid")
    data = spec.load(sample_config)

    assert data["kind"] == "form"
    (section,) = data["sections"]
    fields = {f["key"]: f for f in section["fields"]}
    assert set(fields) == {"Enabled", "Multiplier", "Label", "Whitelist"}
    assert fields["Enabled"]["type"] == "int"
    assert fields["Multiplier"]["type"] == "float"
    assert fields["Label"]["type"] == "str"
    # A scalar list is a real array, edited item by item (plans/76), never a
    # comma-joined string — a class name may contain anything.
    assert fields["Whitelist"]["type"] == "list"
    assert fields["Whitelist"]["item"] == "str"
    assert fields["Whitelist"]["value"] == FLAT["Whitelist"]
    # m_Version is Expansion's schema version, not a setting; the heading says
    # where it went rather than dropping it silently.
    assert "m_Version is on the Raw tab" in section["note"]


def test_nested_keys_travel_as_a_tree_beside_the_fields(sample_config):
    _write(_mission_file(sample_config, "AIPatrolSettings.json"), NESTED)
    data = editors.get_editor("expansion-ai-patrols").load(sample_config)
    (section,) = data["sections"]
    assert [f["key"] for f in section["fields"]] == ["Enabled"]
    # plans/76 step 2: no longer named as Raw-only; the form renders them.
    assert data["nested"] == {
        "Patrols": NESTED["Patrols"],
        "LoadBalancingCategories": NESTED["LoadBalancingCategories"],
    }
    assert "LoadBalancingCategories" not in section["note"]
    assert "m_Version is on the Raw tab" in section["note"]


def test_a_nested_edit_is_saved_on_the_file_s_types(sample_config):
    path = _write(_mission_file(sample_config, "AIPatrolSettings.json"), NESTED)
    spec = editors.get_editor("expansion-ai-patrols")
    base = spec.load(sample_config)["nested"]
    patrols = json.loads(json.dumps(base["Patrols"]))
    patrols[0]["Positions"][0][1] = "7"
    patrols.append({**patrols[0], "Name": "two"})
    spec.save(sample_config, {"$nested": {"Patrols": patrols}, "$nested_base": base})

    after = json.loads(path.read_text())
    assert after["Patrols"] == [
        {"Name": "one", "Positions": [[1, 7, 3]]},
        {"Name": "two", "Positions": [[1, 7, 3]]},
    ]
    assert after["LoadBalancingCategories"] == {"a": 1}
    assert after["m_Version"] == 32


def test_a_nested_save_over_a_newer_file_is_refused(sample_config):
    path = _write(_mission_file(sample_config, "AIPatrolSettings.json"), NESTED)
    spec = editors.get_editor("expansion-ai-patrols")
    base = spec.load(sample_config)["nested"]
    _write(path, {**NESTED, "Patrols": [*NESTED["Patrols"], {"Name": "x", "Positions": []}]})
    with pytest.raises(ValueError, match="changed in the file"):
        spec.save(sample_config, {"$nested": {"Patrols": []}, "$nested_base": base})
    assert len(json.loads(path.read_text())["Patrols"]) == 2


def test_a_nested_save_never_adds_a_key_or_reaches_the_version(sample_config):
    path = _write(_mission_file(sample_config, "AIPatrolSettings.json"), NESTED)
    spec = editors.get_editor("expansion-ai-patrols")
    base = spec.load(sample_config)["nested"]
    with pytest.raises(ValueError, match="has no key 'b'"):
        spec.save(
            sample_config,
            {"$nested": {"LoadBalancingCategories": {"a": 1, "b": 2}}, "$nested_base": base},
        )
    spec.save(sample_config, {"$nested": {"m_Version": 1}, "$nested_base": {"m_Version": 32}})
    assert json.loads(path.read_text()) == NESTED


def test_save_patches_and_never_drops_the_nested_content(sample_config):
    path = _write(_mission_file(sample_config, "AIPatrolSettings.json"), NESTED)
    spec = editors.get_editor("expansion-ai-patrols")
    spec.save(sample_config, {"Enabled": "0"})

    after = json.loads(path.read_text())
    assert after["Enabled"] == 0
    # The 70 kB of patrol data a real file carries here survives a form save.
    assert after["Patrols"] == NESTED["Patrols"]
    assert after["LoadBalancingCategories"] == NESTED["LoadBalancingCategories"]
    assert after["m_Version"] == 32


def test_save_keeps_each_value_on_the_type_the_file_uses(sample_config):
    path = _write(_profile_file(sample_config, "RaidSettings.json"), FLAT)
    editors.get_editor("expansion-raid").save(
        sample_config,
        {
            "Enabled": "1",
            "Multiplier": "3",
            "Label": "changed",
            "Whitelist": '["OnlyThis"]',
        },
    )
    after = json.loads(path.read_text())
    assert after["Enabled"] == 1 and isinstance(after["Enabled"], int)
    assert after["Multiplier"] == 3.0 and isinstance(after["Multiplier"], float)
    assert after["Label"] == "changed"
    assert after["Whitelist"] == ["OnlyThis"]


def test_save_refuses_to_invent_keys_or_touch_the_version(sample_config):
    path = _write(_profile_file(sample_config, "RaidSettings.json"), FLAT)
    editors.get_editor("expansion-raid").save(
        sample_config, {"NotAThing": 5, "m_Version": 1, "Enabled": "0"}
    )
    after = json.loads(path.read_text())
    assert "NotAThing" not in after
    assert after["m_Version"] == 9
    assert after["Enabled"] == 0


def test_a_number_list_is_edited_item_by_item(sample_config):
    """plans/76 step 1: a list of numbers is a list field with whole-number
    items, and a decimal in one is refused with the item named."""
    path = _profile_file(sample_config, "RaidSettings.json")
    _write(path, {**FLAT, "Hours": [1, 2], "Mixed": [1, "a"]})
    (section,) = editors.get_editor("expansion-raid").load(sample_config)["sections"]
    fields = {f["key"]: f for f in section["fields"]}
    assert (fields["Hours"]["type"], fields["Hours"]["item"]) == ("list", "int")
    # A mixed list has no item type, so it stays JSON text.
    assert fields["Mixed"]["value"] == '[1, "a"]'

    editors.get_editor("expansion-raid").save(sample_config, {"Hours": [1, "3"]})
    assert json.loads(path.read_text())["Hours"] == [1, 3]
    with pytest.raises(ValueError, match="Hours: item 2: .*whole number"):
        editors.get_editor("expansion-raid").save(sample_config, {"Hours": [1, "2.5"]})


def test_a_bad_list_names_the_field(sample_config):
    _write(_profile_file(sample_config, "RaidSettings.json"), FLAT)
    with pytest.raises(ValueError, match="Whitelist: expected a list"):
        editors.get_editor("expansion-raid").save(sample_config, {"Whitelist": "not json"})


@pytest.mark.parametrize(
    "values, message",
    [
        # What the browser sends for a cleared number input — measured
        # 2026 Q3, when the toast read "could not convert string to float: ''".
        ({"Enabled": ""}, "Enabled: needs a number"),
        ({"Multiplier": "  "}, "Multiplier: needs a number"),
        ({"Enabled": "abc"}, "Enabled: 'abc' is not a number"),
        # float("inf") parses: an int field raised OverflowError with no field
        # name, and a float field wrote `Infinity` — not JSON at all.
        ({"Enabled": "inf"}, "Enabled: 'inf' is not a number"),
        ({"Multiplier": "1e999"}, "Multiplier: '1e999' is not a number"),
        ({"Multiplier": "nan"}, "Multiplier: 'nan' is not a number"),
        # int() used to cut this to 4 and save it without a word.
        ({"Enabled": "4.5"}, "Enabled: '4.5' is not a whole number"),
    ],
)
def test_a_bad_number_is_refused_in_words_and_writes_nothing(sample_config, values, message):
    path = _write(_profile_file(sample_config, "RaidSettings.json"), FLAT)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        editors.get_editor("expansion-raid").save(sample_config, values)
    assert path.read_bytes() == before


def test_saving_a_file_that_does_not_exist_says_why(sample_config):
    with pytest.raises(RuntimeError, match="start the server once"):
        editors.get_editor("expansion-raid").save(sample_config, {"Enabled": "1"})


AI_LOCATIONS = {
    "m_Version": 4,
    "RoamingLocations": [
        {"Name": "Camp", "Position": [100.5, 2.0, 200.25], "Radius": 50.0, "Type": "Camp", "Enabled": 1},
        {"Name": "Lake", "Position": [300.0, 1.5, 400.0], "Radius": 80.0, "Type": "Lake", "Enabled": 0},
    ],
    "NoGoAreas": [{"Name": "Base", "Position": [1.0, 0.0, 2.0], "Radius": 10.0, "Height": 5.0}],
}


def test_the_two_list_heavy_files_are_forms_with_a_tree(sample_config):
    """plans/76 step 2b: AI Locations and Social Media were raw text because
    their content is all lists of objects; the tree shows those now."""
    _write(_mission_file(sample_config, "AILocationSettings.json"), AI_LOCATIONS)
    spec = editors.get_editor("expansion-ai-locations")
    assert spec.kind == "form"
    data = spec.load(sample_config)
    assert data["kind"] == "form"
    assert data["nested"] == {
        "RoamingLocations": AI_LOCATIONS["RoamingLocations"],
        "NoGoAreas": AI_LOCATIONS["NoGoAreas"],
    }
    (section,) = data["sections"]
    assert section["fields"] == []
    assert "m_Version is on the Raw tab" in section["note"]

    social = {"m_Version": 2, "News": [{"Title": "Hi", "Text": "x", "Url": "https://a"}]}
    _write(_profile_file(sample_config, "SocialMediaSettings.json"), social)
    social_spec = editors.get_editor("expansion-socialmedia")
    assert social_spec.kind == "form"
    assert social_spec.load(sample_config)["nested"] == {"News": social["News"]}


def test_an_ai_location_edit_keeps_types_and_the_rest_of_the_file(sample_config):
    path = _write(_mission_file(sample_config, "AILocationSettings.json"), AI_LOCATIONS)
    spec = editors.get_editor("expansion-ai-locations")
    base = spec.load(sample_config)["nested"]
    locations = json.loads(json.dumps(base["RoamingLocations"]))
    locations[1]["Radius"] = "120"
    locations[1]["Enabled"] = "1"
    del locations[0]
    spec.save(sample_config, {"$nested": {"RoamingLocations": locations}, "$nested_base": base})

    after = json.loads(path.read_text())
    assert after["RoamingLocations"] == [
        {"Name": "Lake", "Position": [300.0, 1.5, 400.0], "Radius": 120.0, "Type": "Lake", "Enabled": 1},
    ]
    assert isinstance(after["RoamingLocations"][0]["Radius"], float)
    assert isinstance(after["RoamingLocations"][0]["Enabled"], int)
    assert after["m_Version"] == 4
    assert after["NoGoAreas"] == AI_LOCATIONS["NoGoAreas"]


def test_an_ai_location_save_over_a_newer_file_is_refused(sample_config):
    path = _write(_mission_file(sample_config, "AILocationSettings.json"), AI_LOCATIONS)
    spec = editors.get_editor("expansion-ai-locations")
    base = spec.load(sample_config)["nested"]
    newer = {**AI_LOCATIONS, "RoamingLocations": AI_LOCATIONS["RoamingLocations"][:1]}
    _write(path, newer)
    with pytest.raises(ValueError, match="RoamingLocations changed in the file"):
        spec.save(sample_config, {"$nested": {"RoamingLocations": []}, "$nested_base": base})
    assert json.loads(path.read_text()) == newer


def test_the_raw_tab_points_at_the_file_even_before_it_exists(sample_config):
    spec = editors.get_editor("expansion-core")
    (target,) = spec.raw_targets(sample_config)
    assert target.name == "CoreSettings.json"
    assert target.language == "json"


# --- plans/76: an empty list of objects is a tree with a template ----------

STOCK_AI_LOCATIONS = {
    "m_Version": 4,
    "RoamingLocations": AI_LOCATIONS["RoamingLocations"],
    "ExcludedRoamingBuildings": [],
    "NoGoAreas": [],
}


def test_an_empty_list_of_objects_is_a_tree_with_a_template(sample_config):
    """Stock AILocationSettings writes `NoGoAreas: []`. As a list field it took
    names, and a typed name was a string where Expansion reads an object."""
    _write(_mission_file(sample_config, "AILocationSettings.json"), STOCK_AI_LOCATIONS)
    data = editors.get_editor("expansion-ai-locations").load(sample_config)
    assert data["nested"]["NoGoAreas"] == []
    assert data["nested_templates"] == {
        "NoGoAreas": {"Name": "", "Position": [0.0, 0.0, 0.0], "Radius": 0.0, "Height": 0.0}
    }
    (section,) = data["sections"]
    # A list of names that is empty stays a list field.
    assert [f["key"] for f in section["fields"]] == ["ExcludedRoamingBuildings"]
    assert section["fields"][0]["type"] == "list"
    assert "m_Version is on the Raw tab" in section["note"]


def test_a_list_with_entries_gets_no_template(sample_config):
    _write(_mission_file(sample_config, "AILocationSettings.json"), AI_LOCATIONS)
    data = editors.get_editor("expansion-ai-locations").load(sample_config)
    assert data["nested_templates"] == {}
    assert data["nested"]["NoGoAreas"] == AI_LOCATIONS["NoGoAreas"]


def test_an_entry_added_to_an_empty_list_is_written_on_the_template_s_types(sample_config):
    path = _write(_mission_file(sample_config, "AILocationSettings.json"), STOCK_AI_LOCATIONS)
    spec = editors.get_editor("expansion-ai-locations")
    data = spec.load(sample_config)
    entry = {"Name": "Base", "Position": ["1000", "5", "2000.5"], "Radius": "50", "Height": "10"}
    spec.save(
        sample_config,
        {"$nested": {"NoGoAreas": [entry]}, "$nested_base": data["nested"]},
    )
    after = json.loads(path.read_text())
    (area,) = after["NoGoAreas"]
    assert area == {"Name": "Base", "Position": [1000.0, 5.0, 2000.5], "Radius": 50.0, "Height": 10.0}
    assert all(isinstance(v, float) for v in area["Position"])
    assert isinstance(area["Radius"], float)
    assert after["RoamingLocations"] == AI_LOCATIONS["RoamingLocations"]
    assert after["m_Version"] == 4
    # The template the payload carries is not changed by a save.
    assert exp.EMPTY_OBJECT_LISTS["AILocationSettings.json"]["NoGoAreas"]["Position"] == [0.0, 0.0, 0.0]


def test_an_entry_for_an_empty_list_keeps_the_template_s_keys(sample_config):
    path = _write(
        _mission_file(sample_config, "SafeZoneSettings.json"),
        {"m_Version": 9, "Enabled": 1, "CircleZones": [], "PolygonZones": [], "CylinderZones": []},
    )
    spec = editors.get_editor("expansion-safezone")
    data = spec.load(sample_config)
    assert set(data["nested_templates"]) == {"CircleZones", "PolygonZones", "CylinderZones"}
    base = data["nested"]
    with pytest.raises(ValueError, match="has no key 'Colour'"):
        spec.save(
            sample_config,
            {
                "$nested": {"CircleZones": [{"Center": [1, 2, 3], "Radius": 5, "Colour": 1}]},
                "$nested_base": base,
            },
        )
    with pytest.raises(ValueError, match="expected an object"):
        spec.save(sample_config, {"$nested": {"CircleZones": ["Camp"]}, "$nested_base": base})
    spec.save(
        sample_config,
        {
            "$nested": {"PolygonZones": [{"Positions": [[1, 2, 3], ["4", "5", "6.5"]]}]},
            "$nested_base": base,
        },
    )
    after = json.loads(path.read_text())
    assert after["PolygonZones"] == [{"Positions": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.5]]}]
    assert after["CircleZones"] == [] and after["Enabled"] == 1


def test_an_empty_list_of_objects_takes_no_names_as_a_field(sample_config):
    """A tree key is saved only through $nested; as a field value (an old tab
    that still shows a list of names) it is ignored, so no string lands where
    Expansion reads an object."""
    path = _write(_mission_file(sample_config, "AILocationSettings.json"), STOCK_AI_LOCATIONS)
    editors.get_editor("expansion-ai-locations").save(
        sample_config, {"NoGoAreas": ["Camp"], "ExcludedRoamingBuildings": ["Land_Shed"]}
    )
    after = json.loads(path.read_text())
    assert after["NoGoAreas"] == []
    assert after["ExcludedRoamingBuildings"] == ["Land_Shed"]


def test_every_template_names_a_file_this_editor_knows():
    filenames = {e.filename for e in exp.SETTINGS}
    assert set(exp.EMPTY_OBJECT_LISTS) <= filenames
    for lists in exp.EMPTY_OBJECT_LISTS.values():
        for template in lists.values():
            assert isinstance(template, dict) and template


@pytest.mark.parametrize(
    "filename, editor, key",
    [
        ("ChatSettings.json", "expansion-chat", "BlacklistedWords"),
        ("SafeZoneSettings.json", "expansion-safezone", "ForceSZCleanup_ExcludedItems"),
        ("TerritorySettings.json", "expansion-territory", "Excluded"),
    ],
)
def test_a_list_of_names_stays_a_list_field_when_empty(sample_config, filename, editor, key):
    where = next(e for e in exp.SETTINGS if e.filename == filename).where
    file = (_profile_file if where == "profile" else _mission_file)(sample_config, filename)
    _write(file, {"m_Version": 1, key: []})
    data = editors.get_editor(editor).load(sample_config)
    (section,) = data["sections"]
    assert [(f["key"], f["type"]) for f in section["fields"]] == [(key, "list")]
    assert data["nested"] == {} and data["nested_templates"] == {}
