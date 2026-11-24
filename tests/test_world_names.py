"""Place names from an official map's world config (app/maps/world_names.py).

The config.bin and the PBO are invented here in the raP and PBO layouts, for
invented terrains (tests/conftest.py::INVENTED_TERRAINS); no file comes from
a real map.
"""

from __future__ import annotations

import struct

import pytest

from app.maps import world_names
from app.maps.locations import load_locations
from tests.test_terrain_map import pbo_bytes


def rap_bytes(root: dict) -> bytes:
    """A raP config.bin for *root*: a dict is a class, a str, int or float a
    value, a list of floats an array. A class's ``"__parent__"`` key is the
    class it inherits from, not an entry."""
    buf = bytearray(b"\x00raP" + struct.pack("<III", 0, 8, 0))

    def write_class(body: dict) -> int:
        start = len(buf)
        parent = body.get("__parent__", "")
        body = {k: v for k, v in body.items() if k != "__parent__"}
        buf.extend(parent.encode() + b"\x00" + bytes([len(body)]))
        patches = []
        for key, value in body.items():
            name = key.encode() + b"\x00"
            if isinstance(value, dict):
                buf.extend(b"\x00" + name)
                patches.append((len(buf), value))
                buf.extend(b"\x00\x00\x00\x00")
            elif isinstance(value, str):
                buf.extend(b"\x01\x00" + name + value.encode() + b"\x00")
            elif isinstance(value, int):
                buf.extend(b"\x01\x02" + name + struct.pack("<i", value))
            elif isinstance(value, float):
                buf.extend(b"\x01\x01" + name + struct.pack("<f", value))
            else:
                buf.extend(b"\x02" + name + bytes([len(value)]))
                for v in value:
                    buf.extend(b"\x01" + struct.pack("<f", v))
        for pos, child in patches:
            struct.pack_into("<I", buf, pos, write_class(child))
        return start

    write_class(root)
    return bytes(buf)


def world_config(names: dict) -> bytes:
    return rap_bytes({"CfgWorlds": {"CAWorld": {}, "TestMap": {"Names": names}}})


def install(server_dir, data: bytes):
    addons = server_dir / "addons"
    addons.mkdir(parents=True, exist_ok=True)
    (addons / "worlds_testmap.pbo").write_bytes(pbo_bytes({"config.bin": data, "testmap.wrp": b"OPRW"}))


@pytest.fixture(autouse=True)
def _fresh(invented_terrains_allowed):
    world_names._cache.clear()


NAMES = {
    "Settlement_Bigtown": {"name": "Bigtown", "position": [1000.5, 2000.25], "type": "NameCity"},
    "Settlement_Hamlet": {"name": "", "position": [300.0, 400.0], "type": "NameVillage"},
    "Vegetation_1": {"name": "", "position": [5.0, 5.0], "type": "VegetationFir"},
    "Hill_Top": {"name": "$STR_HILL", "position": [7.0, 8.0], "type": "Hill"},
}


def test_an_official_map_reads_its_place_names_from_its_world_config(tmp_path):
    install(tmp_path, world_config(NAMES))
    assert world_names.world_names(tmp_path, "testmap") == [
        {"name": "Bigtown", "x": 1000.5, "z": 2000.25, "type": "City"},
        {"name": "Hamlet", "x": 300.0, "z": 400.0, "type": "Village"},
        {"name": "Top", "x": 7.0, "z": 8.0, "type": "Hill"},
    ]


def test_load_locations_falls_back_to_the_world_config(tmp_path):
    install(tmp_path, world_config(NAMES))
    places = load_locations("TestMap", tmp_path)
    assert [(p.name, p.type) for p in places] == [("Bigtown", "City"), ("Hamlet", "Village"), ("Top", "Hill")]
    assert load_locations("testmap") == []  # no server dir, no names


def test_a_map_off_the_allowlist_is_never_read(tmp_path, monkeypatch):
    install(tmp_path, world_config(NAMES))
    monkeypatch.setattr(world_names, "terrain_layer_allowed", lambda terrain: False)
    assert world_names.world_names(tmp_path, "testmap") == []


@pytest.mark.parametrize("data", [
    b"not a config",
    world_config(NAMES)[:60],
    rap_bytes({"CfgWorlds": {"OtherMap": {"Names": NAMES}}}),
])
def test_an_unreadable_or_foreign_world_config_is_no_names(tmp_path, data):
    install(tmp_path, data)
    assert world_names.world_names(tmp_path, "testmap") == []
