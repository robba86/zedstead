"""One source of truth for "which maps does zsm know about".

`VANILLA_MAPS` / `WORKSHOP_MAPS` in `app.shared.seed_data` are the **catalog**:
the maps a user can install, keyed by display name. Everything the app knows
about a map once it is installed is keyed by **terrain** instead — the mission
folder's suffix (`dayzOffline.enoch` -> `enoch`) — because that is what the
running server, every mission XML and every map image actually carry.

This module joins the two, so the map lists scattered around the app (the Mod
XML page's Map picker, the map editor's map list, the mod-XML map-variant
filter) all derive from the catalog instead of repeating it. Adding a map to
the catalog is then enough for it to appear in all of them.

**Adding a map**: add it to `VANILLA_MAPS` / `WORKSHOP_MAPS` (+
`MAP_DESCRIPTIONS`) and give its terrain a `MAP_SIZES` entry. Add an
`EXTRA_TERRAIN_TOKENS` alias only when mods spell the map in a
filename in a way neither the terrain nor the display name covers, and a
`MAP_CALIBRATION` entry (`app/spawn_editor/converters.py`) only to fine-tune
the map editor's overlay — it falls back to Chernarus' calibration, which is
self-consistent for any map whose size is known.

An announced-but-unshipped official map has no mission, so it has no terrain
and is deliberately absent here: it is a card on the Maps page, not something
a mission file or a map image can refer to.

No map image ships with zsm: a map's picture is its author's work. An official
map (`TERRAIN_LAYER_MAPS`) opens on the terrain drawn from the server's own
world file, every other map on the building map, and an admin can upload their
own image (plans/69). A map whose maker agreed (`CLEARED_MAPS`) gets what they
cleared (image, heights, roads), with the maker's credit.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.shared.seed_data import (
    MAP_SIZES,
    VANILLA_MAPS,
    WORKSHOP_MAPS,
)

# Fallback edge length in metres for a map with no MAP_SIZES entry (Chernarus').
DEFAULT_MAP_SIZE = 15360

# Filename spellings mods use for a map that neither its terrain nor its
# display name yields. Everything else is derived — `Deer Isle` already gives
# both `deerisle` and `deer_isle`, `Livonia` already covers terrain `enoch`.
EXTRA_TERRAIN_TOKENS: dict[str, set[str]] = {
    "chernarusplus": {"cherno"},
    "takistanplus": {"takistan"},
}


@dataclass(frozen=True)
class MapEntry:
    """A terrain zsm can install, with the catalog entries that install it."""

    terrain: str
    name: str
    #: Every catalog entry landing on this terrain, e.g. Namalsk + Namalsk
    #: Hardcore, which ship the same terrain with different mission presets.
    variants: tuple[str, ...]
    #: Filename tokens that mean "this map" — see `xml_classify`'s variant filter.
    tokens: frozenset[str]
    size: int


def terrain_from_mission(mission: str) -> str:
    """``dayzOffline.chernarusplus`` -> ``chernarusplus`` (lowercased)."""
    return mission.rsplit(".", 1)[-1].lower()


def _mission_template(data: dict) -> str | None:
    """The mission folder name of a catalog entry, vanilla or workshop."""
    mission = data.get("mission")
    if isinstance(mission, dict):
        return mission.get("template")
    return mission


def _name_tokens(name: str) -> set[str]:
    """``Deer Isle`` -> ``{deerisle, deer_isle}`` — both spellings mods use."""
    low = name.lower()
    return {low.replace(" ", ""), low.replace(" ", "_")}


def _build() -> tuple[MapEntry, ...]:
    names_by_terrain: dict[str, list[str]] = {}
    for name, data in list(VANILLA_MAPS.items()) + list(WORKSHOP_MAPS.items()):
        template = _mission_template(data)
        if not template:
            continue  # announced, not shipped — no mission, so no terrain
        names_by_terrain.setdefault(terrain_from_mission(template), []).append(name)

    entries = []
    for terrain, names in names_by_terrain.items():
        tokens = {terrain} | set(EXTRA_TERRAIN_TOKENS.get(terrain, set()))
        for name in names:
            tokens |= _name_tokens(name)
        entries.append(
            MapEntry(
                terrain=terrain,
                name=names[0],
                variants=tuple(names),
                tokens=frozenset(tokens),
                size=MAP_SIZES.get(terrain, DEFAULT_MAP_SIZE),
            )
        )
    return tuple(entries)


# Catalog order: the vanilla maps first, then the workshop ones.
KNOWN_MAPS: tuple[MapEntry, ...] = _build()

MAPS_BY_TERRAIN: dict[str, MapEntry] = {entry.terrain: entry for entry in KNOWN_MAPS}

#: Every accepted filename/terrain token -> the terrain it names.
TERRAIN_BY_TOKEN: dict[str, str] = {
    token: entry.terrain for entry in KNOWN_MAPS for token in sorted(entry.tokens)
}


def known_terrains() -> list[str]:
    """Every terrain zsm ships map knowledge for, in catalog order."""
    return [entry.terrain for entry in KNOWN_MAPS]


#: Terrains that live inside the base game data, so a server carries their
#: files whether or not it runs them — which is why a Chernarus run logs an
#: animation config for Sakhal. Derived from the catalog, never a second list.
VANILLA_TERRAINS: frozenset[str] = frozenset(
    terrain_from_mission(template)
    for template in (_mission_template(data) for data in VANILLA_MAPS.values())
    if template
)

#: Terrains whose world file and navmesh the map editor may read in full
#: (`terrain_layer_allowed`): heights, roads, navmesh and place names. The
#: official maps only, derived from the catalog.
TERRAIN_LAYER_MAPS: frozenset[str] = VANILLA_TERRAINS


@dataclass(frozen=True)
class MapCredit:
    """The credit a map's maker asked for, shown wherever their data is drawn."""

    text: str
    link_label: str = ""
    url: str = ""


@dataclass(frozen=True)
class ClearedMap:
    """What a community map's maker allows the map editor to use."""

    credit: MapCredit
    #: The maker's satellite image, downloaded once from zedstead.com.
    image_url: str | None = None
    #: The maker's own height grid (ESRI ASCII, gzipped), downloaded once
    #: from zedstead.com. It replaces the world file as the heights' source.
    heights_url: str | None = None
    #: Heights read from the server's world file, for a map without a grid
    #: from its maker.
    world_heights: bool = False
    #: Roads read from the server's world file.
    roads: bool = False
    #: One point (x, z in metres) inside each lake above sea level. The
    #: heights hold a lake's bed but not its water, and most dips in them are
    #: dry, so the terrain map fills only the dips these points name
    #: (webui `terrainLakes.js`). Read off the maker's image by eye.
    lakes: tuple[tuple[float, float], ...] = ()


#: Community maps whose maker agreed in writing (plans/65, quoted verbatim
#: there). Never add one without that evidence. KubeloLive, 2026-11-01: the
#: satellite image and the heightmap of both maps, in the map editor only.
#: 2026-11-06: he sent a new Banov image and Banov's height grid, and allowed
#: the roads ("you can added roads"; the maintainer reads it for both maps).
#: The navmesh, place names, other assets and PBO contents stay excluded.
_KUBELO = MapCredit(
    text="Banov & Banov Frost created by KubeloLive",
    link_label="Official project",
    url="https://www.patreon.com/KubeloLive",
)
CLEARED_MAPS: dict[str, ClearedMap] = {
    # His Banov grid (banov.asc) came privately and is not hosted: a file on
    # zedstead.com is a public download, which he has not agreed to. Heights come
    # from the world file instead; `heights_url` is where the grid would go.
    "banov": ClearedMap(
        _KUBELO,
        image_url="https://zedstead.com/maps/banov.webp",
        world_heights=True,
        roads=True,
        lakes=((9850, 10500), (13720, 9150), (6930, 4300), (3080, 7700), (2470, 13250)),
    ),
    "banovfrost": ClearedMap(
        _KUBELO,
        image_url="https://zedstead.com/maps/banovfrost.webp",
        world_heights=True,
        roads=True,
    ),
}


def cleared_map(terrain: str | None) -> ClearedMap | None:
    """What *terrain*'s maker cleared, ``None`` for an official or uncleared map."""
    return CLEARED_MAPS.get(terrain.lower()) if terrain else None


def terrain_layer_allowed(terrain: str | None) -> bool:
    """Whether the map editor may read all of *terrain*'s world data.

    The official maps only. The navmesh (`navmesh_map`) and place names
    (`world_names`) check this before they touch a file.
    """
    return bool(terrain) and terrain.lower() in TERRAIN_LAYER_MAPS


def maker_heights_url(terrain: str | None) -> str | None:
    """The height grid *terrain*'s maker gave Zedstead, if there is one."""
    cleared = cleared_map(terrain)
    return cleared.heights_url if cleared else None


def heights_allowed(terrain: str | None) -> bool:
    """Whether the terrain layer may draw *terrain*'s heights, from any source.

    Every official map, and a community map whose maker cleared its heights
    (`CLEARED_MAPS`). The /terrain-heights route checks this.
    """
    cleared = cleared_map(terrain)
    return terrain_layer_allowed(terrain) or bool(
        cleared and (cleared.heights_url or cleared.world_heights)
    )


def world_heights_allowed(terrain: str | None) -> bool:
    """Whether *terrain*'s heights may be read from the server's world file."""
    cleared = cleared_map(terrain)
    return terrain_layer_allowed(terrain) or bool(cleared and cleared.world_heights)


def roads_allowed(terrain: str | None) -> bool:
    """Whether *terrain*'s roads may be read from the server's world file.

    `road_map.terrain_roads` and the /roads route check this.
    """
    cleared = cleared_map(terrain)
    return terrain_layer_allowed(terrain) or bool(cleared and cleared.roads)


def world_file_allowed(terrain: str | None) -> bool:
    """Whether `terrain_map.world_file` may open *terrain*'s world file at all.

    For its heights or for its roads; each reader checks its own gate too.
    """
    return world_heights_allowed(terrain) or roads_allowed(terrain)


def editor_features(terrain: str | None, encrypted_world: bool = False) -> dict:
    """What the map editor may draw for *terrain*, for the Maps page's cards.

    The registry's gates, plus *encrypted_world* (`terrain_map.world_is_encrypted`):
    an encrypted world file's terrain comes from the navmesh, which has no
    roads. A server without the world file still draws less, which the editor
    explains. Every map takes the admin's own image.
    """
    cleared = cleared_map(terrain)
    return {
        "maker_image": bool(cleared and cleared.image_url),
        "terrain": heights_allowed(terrain),
        "terrain_from_navmesh": encrypted_world and heights_allowed(terrain),
        "roads": roads_allowed(terrain) and not encrypted_world,
        "names": terrain_layer_allowed(terrain),
        "credit": cleared.credit.text if cleared else None,
    }


def display_name(terrain: str) -> str:
    """Display name for a terrain, e.g. ``enoch`` -> ``Livonia``.

    A community map zsm doesn't ship is titled from its terrain rather than
    dropped — the map editor happily loads one, so its name has to render.
    """
    entry = MAPS_BY_TERRAIN.get(terrain)
    return entry.name if entry else terrain.title()


def map_choices() -> list[dict[str, str]]:
    """``[{terrain, name}]`` in catalog order — a map picker's option list."""
    return [{"terrain": entry.terrain, "name": entry.name} for entry in KNOWN_MAPS]


def terrain_labels() -> dict[str, str]:
    """Every accepted map spelling -> display name, for labelling a stored value.

    A map scope saved as ``livonia`` (an alias `known_terrain_tokens` accepts)
    still has to read as "Livonia" in the UI, not as a raw token.
    """
    return {token: display_name(terrain) for token, terrain in TERRAIN_BY_TOKEN.items()}


def terrain_for_token(token: str) -> str | None:
    """The terrain a map spelling names, e.g. ``livonia`` -> ``enoch``."""
    return TERRAIN_BY_TOKEN.get(token.lower())


def map_size(terrain: str) -> int:
    """Terrain edge length in metres, Chernarus' size for an unknown map."""
    return MAP_SIZES.get(terrain, DEFAULT_MAP_SIZE)
