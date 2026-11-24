"""Regression: Banov Frost reuses Banov's location database.

`banovfrost` has no `locations_banovfrost.json` — it is a winter reskin of
Banov with identical world coords. `/api/locations` must alias it to `banov`
so frost locations actually render in the map editor. Zedstead ships no label file
(plans/86), so the tests give Banov one in the user's dir.

The editor reads through `app.maps.locations.load_locations` (plans/60 D), so a
community map's own file in the user's locations dir reaches it as well.
"""

import json

import pytest

from app.maps.locations import LOCATION_TERRAIN_ALIASES
from app.shared import paths
from app.spawn_editor import routes


class _StubSpawnState:
    terrain = "banovfrost"


def _stub_state():
    """A MapEditorState carrying only what /locations touches."""
    return routes.MapEditorState(
        config=None, broadcaster=None, spawn_state=_StubSpawnState()
    )


async def _call_locations(terrain):
    resp = await routes.get_locations(terrain=terrain, me=_stub_state())
    return json.loads(resp.body)


@pytest.fixture
def banov_labels():
    """An admin's own Banov file in ~/.config/zsm/locations/."""
    user_dir = paths.locations_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "locations_banov.json").write_text(json.dumps([
        {"name": "Hill Farm", "x": 1000.0, "z": 2000.0, "type": "Village"},
        {"name": "Lake", "x": 3000.0, "z": 4000.0, "type": "Marine"},
    ]), encoding="utf-8")


def test_banovfrost_aliases_to_banov():
    assert LOCATION_TERRAIN_ALIASES["banovfrost"] == "banov"


def test_banovfrost_returns_banov_locations(banov_labels):
    """The pure helper aliases without needing any editor state."""
    frost = routes.locations_for_terrain("banovfrost")
    banov = routes.locations_for_terrain("banov")

    assert frost, "banovfrost locations should not be empty"
    assert frost == banov


async def test_banovfrost_terrain_from_state(banov_labels):
    """No explicit terrain arg → falls back to spawn_state.terrain, still aliases."""
    frost = await _call_locations(None)
    banov = await _call_locations("banov")

    assert frost
    assert frost == banov


def test_a_community_maps_own_file_is_served():
    """A terrain reads its labels from ~/.config/zsm/locations/."""
    user_dir = paths.locations_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "locations_pnw.json").write_text(json.dumps([
        {"name": "Harbour", "x": 100.0, "z": 200.0, "type": "Village", "label": "HARBOUR TOWN"},
        {"name": "Old Mill", "x": 300.0, "z": 400.0, "type": "Local", "name_ru": "MÜHLE"},
    ]), encoding="utf-8")

    served = routes.locations_for_terrain("pnw")

    assert [(e["name"], e["type"], e.get("label")) for e in served] == [
        ("Harbour", "Village", "HARBOUR TOWN"),
        # the pre-plans/60 field name still reads, for one release
        ("Old Mill", "Local", "MÜHLE"),
    ]
    assert all({"lat", "lng"} <= set(e) for e in served)


def test_a_terrain_is_one_name_never_a_path():
    """plans/68 PATH-4. The terrain becomes part of a filename joined onto
    the label dirs; one that is not a single name reads nothing, even where
    the path it spells exists and holds a valid label file."""
    from app.maps.locations import load_locations

    user_dir = paths.locations_dir()
    (user_dir / "locations_").mkdir(parents=True)
    outside = user_dir.parent / "outside"
    outside.mkdir()
    (outside / "x.json").write_text(json.dumps([
        {"name": "Elsewhere", "x": 1.0, "z": 1.0, "type": "Local"},
    ]), encoding="utf-8")

    assert load_locations("/../../outside/x") == []
    assert routes.locations_for_terrain("/../../outside/x") == []
    (user_dir / "locations_pnw.json").write_text(json.dumps([
        {"name": "Harbour", "x": 1.0, "z": 1.0, "type": "Village"},
    ]), encoding="utf-8")
    assert load_locations("pnw")  # a real name still reads
