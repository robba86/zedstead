"""Tests for app.spawn_editor.converters — game <-> display coordinates."""

from __future__ import annotations

import pytest

from app.spawn_editor.converters import (
    MAP_CALIBRATION,
    game_to_latlng,
    get_map_bounds,
    latlng_to_game,
)


@pytest.mark.parametrize("terrain", sorted(MAP_CALIBRATION))
def test_round_trip_identity(terrain):
    """game -> latlng -> game returns the original coordinates."""
    x, z = 7500.0, 8200.0
    lat, lng = game_to_latlng(x, z, terrain)
    rx, rz = latlng_to_game(lat, lng, terrain)
    assert rx == pytest.approx(x, abs=1e-6)
    assert rz == pytest.approx(z, abs=1e-6)


def test_uncalibrated_terrain_does_not_raise():
    """A community map outside MAP_CALIBRATION must not crash the editor.

    get_terrain_from_mission returns the raw mission suffix, so an uncalibrated
    terrain reaches the converters on every spawn. Regression: this used to
    raise KeyError on MAP_CALIBRATION[terrain].
    """
    lat, lng = game_to_latlng(7500.0, 8200.0, "rostow")
    x, z = latlng_to_game(lat, lng, "rostow")
    assert x == pytest.approx(7500.0, abs=1e-6)
    assert z == pytest.approx(8200.0, abs=1e-6)


def test_uncalibrated_falls_back_to_chernarus():
    """Unknown terrain uses chernarusplus calibration, matching get_map_bounds."""
    assert game_to_latlng(5000.0, 6000.0, "totally-made-up") == game_to_latlng(
        5000.0, 6000.0, "chernarusplus"
    )
    assert latlng_to_game(10.0, 20.0, "totally-made-up") == latlng_to_game(
        10.0, 20.0, "chernarusplus"
    )


# ── the world's bounds ───────────────────────────────


def test_bounds_did_not_move():
    """Markers, writes, the readout and the admin's image all sit on these."""
    bounds = get_map_bounds("chernarusplus")
    assert bounds["south_west"] == pytest.approx([-113.0769, -122.3077], abs=1e-4)
    assert bounds["north_east"] == pytest.approx([123.2308, 114.0], abs=1e-4)


@pytest.mark.parametrize("terrain", sorted(MAP_CALIBRATION))
def test_bounds_match_marker_projection(terrain):
    """The page's canvas layers use the bounds, markers use game_to_latlng:
    both must give the same point, or a marker sits beside its building."""
    bounds = get_map_bounds(terrain)
    assert bounds["south_west"] == list(game_to_latlng(0, 0, terrain))
