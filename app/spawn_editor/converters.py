"""Bidirectional conversion between game coordinates (x, z) and display (lat, lng).

The display coordinate system uses a single scale for both axes so that the
square game world renders as a square image overlay in Leaflet CRS.Simple.

Formula (display):
    display_lng = (game_x - offset_x) / scale
    display_lat = (game_z - offset_z) / scale
"""

from __future__ import annotations

from app.maps.registry import KNOWN_MAPS, map_size

# Per-map display calibration constants.
# scale  — single scale factor for both axes (keeps map square in CRS.Simple)
# offset_x, offset_z — game-coord offsets for the display origin
#
# These numbers cannot misplace anything. Markers and the image bounds
# (`_compute_bounds`) are both projected through them, so changing one moves
# the marker and the image together. Whether a marker lands on the right spot of
# the picture depends on two other things only: `MAP_SIZES` being the real world
# size, and the image covering game (0, 0)..(size, size). Measure a suspected
# misfit against the building map, which draws the mission's own `mapgrouppos.xml`,
# before touching either (plans/60, plans/69).
#
# NOT the list of maps the editor offers — that comes from `app.maps.registry`.
# A map with no entry here borrows Chernarus' calibration, which is
# self-consistent (bounds are computed from the same numbers), so it renders
# correctly as long as its size is known.
MAP_CALIBRATION: dict[str, dict[str, float]] = {
    "chernarusplus": {"scale": 65.00, "offset_x": 7950.0, "offset_z": 7350.0},
    "enoch":         {"scale": 54.17, "offset_x": 6625.0, "offset_z": 6125.0},
    "sakhal":        {"scale": 54.17, "offset_x": 6625.0, "offset_z": 6125.0},
    "nasdara":       {"scale": 69.33, "offset_x": 8480.0, "offset_z": 7840.0},
    "namalsk":       {"scale": 54.17, "offset_x": 6625.0, "offset_z": 6125.0},
    "deerisle":      {"scale": 69.33, "offset_x": 8480.0, "offset_z": 7840.0},
    "banov":         {"scale": 65.00, "offset_x": 7950.0, "offset_z": 7350.0},
    "banovfrost":    {"scale": 65.00, "offset_x": 7950.0, "offset_z": 7350.0},
    "esseker":       {"scale": 54.17, "offset_x": 6625.0, "offset_z": 6125.0},
    "takistanplus":  {"scale": 54.17, "offset_x": 6625.0, "offset_z": 6125.0},
    "bitterroot":    {"scale": 52.00, "offset_x": 6360.0, "offset_z": 5880.0},
}


def _calibration(terrain: str) -> dict[str, float]:
    """Calibration for a terrain, falling back to chernarusplus for uncalibrated
    (community / custom) maps.

    Mirrors get_map_bounds/get_map_size so the spawn editor degrades gracefully
    on an unknown terrain instead of raising KeyError on every coordinate
    conversion — get_terrain_from_mission returns the raw mission suffix, which
    is often a community map outside MAP_CALIBRATION's entries.
    """
    return MAP_CALIBRATION.get(terrain, MAP_CALIBRATION["chernarusplus"])


def _compute_bounds(terrain: str) -> dict[str, list[float]]:
    """Compute image overlay bounds from calibration and map size.

    Returns {"south_west": [lat, lng], "north_east": [lat, lng]}.
    Game origin (0,0) maps to SW corner; (MAP_SIZE, MAP_SIZE) maps to NE corner.
    Never rounded: the page projects its canvas layers from these bounds, and
    the server projects markers with `game_to_latlng`, so rounded bounds put a
    marker up to 0.25 m beside its building at a close zoom.
    """
    size = map_size(terrain)
    return {
        "south_west": list(game_to_latlng(0, 0, terrain)),
        "north_east": list(game_to_latlng(size, size, terrain)),
    }


def game_to_latlng(x: float, z: float, terrain: str) -> tuple[float, float]:
    """Convert game coordinates (x, z) to display (lat, lng).

    Returns (lat, lng).
    """
    cal = _calibration(terrain)
    scale = cal["scale"]
    lng = (x - cal["offset_x"]) / scale
    lat = (z - cal["offset_z"]) / scale
    return (lat, lng)


def latlng_to_game(lat: float, lng: float, terrain: str) -> tuple[float, float]:
    """Convert display (lat, lng) to game coordinates (x, z).

    Inverse of game_to_latlng. Returns (x, z).
    """
    cal = _calibration(terrain)
    scale = cal["scale"]
    x = lng * scale + cal["offset_x"]
    z = lat * scale + cal["offset_z"]
    return (x, z)


# Pre-computed bounds for every map zsm knows about, calibrated or not.
MAP_BOUNDS: dict[str, dict[str, list[float]]] = {
    terrain: _compute_bounds(terrain)
    for terrain in dict.fromkeys(
        [entry.terrain for entry in KNOWN_MAPS] + list(MAP_CALIBRATION)
    )
}


def get_map_bounds(terrain: str) -> dict[str, list[float]]:
    """The world's bounds in Leaflet: game (0, 0) to (size, size).

    Falls back to chernarusplus bounds if terrain is unknown.
    """
    return MAP_BOUNDS.get(terrain, MAP_BOUNDS["chernarusplus"])


def get_map_size(terrain: str) -> int:
    """Get map size in game units (meters)."""
    return map_size(terrain)


def same_as_shown(current: float, new: float, digits: int = 2) -> bool:
    """*new* is *current*, or the rounding of it the editor displayed.

    A popup sends back every field, and an untouched one arrives as what it
    showed (payloads round positions to 2 decimals, orientations to 1), so
    that is not an edit (F10, plans/54 A and D).
    """
    return new == current or new == round(current, digits)


def keep_or_round(current: object, new: object, digits: int = 2) -> object:
    """The value to store when a request sets a number to *new*.

    *current* itself, untouched, when *new* is only its displayed rounding;
    otherwise *new* rounded to *digits*. Rounding an untouched value rewrote
    a file's numbers nobody edited (F75).
    """
    new = float(new)
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        if same_as_shown(current, new, digits):
            return current
    return round(new, digits)
