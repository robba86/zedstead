"""The map catalog is the only place a map is listed.

Every map picker in the app derives from `app.maps.registry`, which derives
from `VANILLA_MAPS` / `WORKSHOP_MAPS`. These tests fail when a list drifts back
into a hardcoded copy — adding a map to the catalog has to be enough.
"""

from app.maps import registry
from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS


def _catalog_terrains() -> set[str]:
    terrains = set()
    for data in list(VANILLA_MAPS.values()) + list(WORKSHOP_MAPS.values()):
        mission = data.get("mission")
        template = mission.get("template") if isinstance(mission, dict) else mission
        if template:
            terrains.add(registry.terrain_from_mission(template))
    return terrains


def test_registry_covers_every_installable_map():
    assert set(registry.known_terrains()) == _catalog_terrains()


def test_unreleased_map_has_no_terrain(unreleased_map):
    """An announced map is a Maps-page card with no mission — nothing keys on it."""
    assert VANILLA_MAPS[unreleased_map]["mission"] is None
    assert unreleased_map not in {e.name for e in registry._build()}


def test_badlands_is_a_shipped_map():
    """DayZ 1.30 shipped Badlands; its mission and size come from that download."""
    entry = registry.MAPS_BY_TERRAIN["nasdara"]
    assert entry.name == "Badlands"
    assert VANILLA_MAPS["Badlands"]["mission"] == "dayzOffline.nasdara" and "released" not in VANILLA_MAPS["Badlands"]
    assert entry.size == 16384
    assert "nasdara" in registry.VANILLA_TERRAINS


def test_variants_of_one_terrain_collapse_to_one_map():
    """Namalsk + Namalsk Hardcore are one terrain, so one row in a picker."""
    entry = registry.MAPS_BY_TERRAIN["namalsk"]
    assert entry.name == "Namalsk"
    assert entry.variants == ("Namalsk", "Namalsk Hardcore")


def test_tokens_cover_terrain_and_display_spellings():
    tokens = registry.MAPS_BY_TERRAIN["enoch"].tokens
    assert {"enoch", "livonia"} <= tokens
    # Two-word names yield both spellings a mod filename may use.
    assert {"deerisle", "deer_isle"} <= registry.MAPS_BY_TERRAIN["deerisle"].tokens
    # ... plus the aliases neither spelling produces.
    assert "cherno" in registry.MAPS_BY_TERRAIN["chernarusplus"].tokens


def test_display_name_falls_back_to_the_terrain():
    assert registry.display_name("enoch") == "Livonia"
    assert registry.display_name("atlantis") == "Atlantis"


def test_terrain_for_token_resolves_aliases():
    assert registry.terrain_for_token("Livonia") == "enoch"
    assert registry.terrain_for_token("atlantis") is None


def test_map_variant_filter_uses_the_registry():
    from app.xml import xml_classify

    assert set(xml_classify.TERRAIN_FILENAME_TOKENS) == set(registry.known_terrains())


def test_manual_xml_accepts_every_registry_spelling():
    from app.xml import xml_manual

    accepted = xml_manual.known_terrain_tokens()
    for entry in registry.KNOWN_MAPS:
        assert entry.tokens <= accepted


def test_map_editor_has_bounds_for_every_known_map():
    """An uncalibrated map still renders — bounds fall back, the size doesn't."""
    from app.spawn_editor.converters import MAP_BOUNDS, get_map_size

    for entry in registry.KNOWN_MAPS:
        assert entry.terrain in MAP_BOUNDS
        assert get_map_size(entry.terrain) == entry.size


def test_map_choices_are_catalog_ordered():
    choices = registry.map_choices()
    assert choices[0] == {"terrain": "chernarusplus", "name": "Chernarus"}
    assert [c["terrain"] for c in choices] == registry.known_terrains()


def test_terrain_labels_name_every_accepted_spelling():
    labels = registry.terrain_labels()
    assert labels["livonia"] == "Livonia"
    assert labels["enoch"] == "Livonia"


def test_the_terrain_layer_is_on_for_the_official_maps_only():
    """The allowlist is the catalog's vanilla terrains: Badlands is nasdara."""
    assert registry.TERRAIN_LAYER_MAPS == {"chernarusplus", "enoch", "sakhal", "nasdara"}
    assert registry.terrain_layer_allowed("ChernarusPlus")
    for entry in registry.KNOWN_MAPS:
        if entry.terrain not in registry.VANILLA_TERRAINS:
            assert not registry.terrain_layer_allowed(entry.terrain), entry.terrain
    assert not registry.terrain_layer_allowed("")
    assert not registry.terrain_layer_allowed(None)


def test_heights_are_on_for_the_official_maps_and_the_cleared_ones():
    """KubeloLive (plans/65): image, world-file heights and roads for both maps; never the navmesh."""
    assert registry.heights_allowed("chernarusplus")
    assert registry.heights_allowed("Banov") and registry.heights_allowed("banovfrost")
    # His grid is not hosted (a public download he has not agreed to).
    assert registry.maker_heights_url("banov") is None
    assert registry.world_heights_allowed("banov") and registry.world_heights_allowed("banovfrost")
    assert registry.roads_allowed("banov") and registry.roads_allowed("banovfrost")
    assert registry.world_file_allowed("banov")
    # The navmesh and place names stay with the official maps.
    assert not registry.terrain_layer_allowed("banov")
    for check in (registry.heights_allowed, registry.roads_allowed, registry.world_file_allowed):
        assert not check("namalsk") and not check(None)
    for terrain, entry in registry.CLEARED_MAPS.items():
        assert terrain in registry.known_terrains(), terrain
        assert entry.credit.text
        for url in (entry.image_url, entry.heights_url):
            assert url is None or url.startswith("https://zedstead.com/maps/")


def test_editor_features_say_what_each_map_draws():
    """The Maps page's cards: official maps draw everything but a maker's image, Banov what KubeloLive cleared."""
    official = registry.editor_features("chernarusplus")
    assert official == {
        "maker_image": False, "terrain": True, "terrain_from_navmesh": False,
        "roads": True, "names": True, "credit": None,
    }
    sakhal = registry.editor_features("sakhal", encrypted_world=True)
    assert sakhal["terrain"] and sakhal["terrain_from_navmesh"] and not sakhal["roads"]
    banov = registry.editor_features("banov")
    assert banov["maker_image"] and banov["terrain"] and banov["roads"] and not banov["names"]
    assert "KubeloLive" in banov["credit"]
    namalsk = registry.editor_features("namalsk")
    assert not any(namalsk[key] for key in ("maker_image", "terrain", "roads", "names"))
