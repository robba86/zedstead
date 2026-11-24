"""The map editor's location labels, and the map sizes they are drawn over.

Zedstead ships no label file (plans/86): a map's place names are its makers' work.
An admin can add `locations_<terrain>.json` in the user's locations dir, and
`app.maps.locations.load_locations` reads it (`tests/test_spawn_editor_locations.py`).
"""

import re
from pathlib import Path

import pytest

from app.maps.locations import LOCATION_TYPES
from app.shared.seed_data import MAP_SIZES
from tests.conftest import CE_1_29, bohemia_ce

ROOT = Path(__file__).resolve().parents[1]
VANILLA_MISSIONS = {
    "chernarusplus": "dayzOffline.chernarusplus",
    "enoch": "dayzOffline.enoch",
    "sakhal": "dayzOffline.sakhal",
}
# A building's world position in mapgrouppos.xml: x, height, z.
_POS = re.compile(r'<group name="[^"]*" pos="([-\d.]+) [-\d.]+ ([-\d.]+)"')


def test_no_label_file_ships():
    assert not sorted((ROOT / "app" / "data").glob("locations_*.json"))


@pytest.mark.parametrize("terrain", sorted(VANILLA_MISSIONS))
def test_every_stock_building_is_inside_the_map(terrain):
    """MAP_SIZES is what the editor stretches the map image over. Bohemia's
    `mapgrouppos.xml` holds every loot building's world position."""
    text = bohemia_ce(CE_1_29, f"{VANILLA_MISSIONS[terrain]}/mapgrouppos.xml")
    points = [(float(x), float(z)) for x, z in _POS.findall(text)]
    assert points
    size = MAP_SIZES[terrain]
    outside = [p for p in points if not (0 <= p[0] < size and 0 <= p[1] < size)]
    assert not outside, f"{terrain}: buildings outside 0..{size}: {outside[:10]}"


def test_every_type_has_a_colour():
    js = (ROOT / "webui" / "src" / "pages" / "mapeditor" / "icons" / "iconRules.js").read_text(encoding="utf-8")
    block = re.search(r"const LOCATION_COLORS = \{(.*?)\}", js, re.S).group(1)
    assert set(re.findall(r"^\s*(\w+):", block, re.M)) == LOCATION_TYPES
