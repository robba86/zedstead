"""Tests for cli.traderplus_vehicles_json (TraderPlusVehiclesConfig parser)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_json

SAMPLE_VEHICLES_CONFIG = {
    "Version": "2.5",
    "VehiclesParts": [
        {
            "VehicleName": "OffroadHatchback",
            "Height": 3,
            "VehicleParts": ["HatchbackDoors_Driver", "HatchbackHood"],
        },
        {
            "VehicleName": "Sedan_02",
            "Height": 2,
            "VehicleParts": ["Sedan_02_Door_1_1"],
        },
    ],
}


def test_parse_vehicles_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusVehiclesConfig.json", SAMPLE_VEHICLES_CONFIG)

    from app.traderplus.traderplus_vehicles_json import parse_vehicles_config

    cfg = parse_vehicles_config(path)

    assert cfg.version == "2.5"
    assert len(cfg.vehicles) == 2

    v1 = cfg.vehicles[0]
    assert v1.vehicle_name == "OffroadHatchback"
    assert v1.height == 3
    assert v1.vehicle_parts == ["HatchbackDoors_Driver", "HatchbackHood"]


def test_vehicles_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusVehiclesConfig.json", SAMPLE_VEHICLES_CONFIG)

    from app.traderplus.traderplus_vehicles_json import (
        add_part,
        add_vehicle,
        parse_vehicles_config,
        remove_part,
        remove_vehicle,
        set_parts,
        update_vehicle,
    )

    cfg = parse_vehicles_config(path)

    # add_vehicle
    v = add_vehicle(cfg, "Truck_01_Covered", height=5)
    assert len(cfg.vehicles) == 3
    assert v.vehicle_name == "Truck_01_Covered"
    assert v.height == 5

    # update_vehicle
    update_vehicle(cfg, 2, "Truck_01_Open", 4)
    assert cfg.vehicles[2].vehicle_name == "Truck_01_Open"
    assert cfg.vehicles[2].height == 4

    # add_part
    add_part(cfg.vehicles[2], "Truck_01_Wheel")
    assert cfg.vehicles[2].vehicle_parts == ["Truck_01_Wheel"]

    # remove_part
    remove_part(cfg.vehicles[2], 0)
    assert cfg.vehicles[2].vehicle_parts == []

    # set_parts
    set_parts(cfg.vehicles[2], ["PartA", "PartB", "PartC"])
    assert cfg.vehicles[2].vehicle_parts == ["PartA", "PartB", "PartC"]

    # remove_vehicle
    remove_vehicle(cfg, 2)
    assert len(cfg.vehicles) == 2

    # out-of-bounds operations are no-ops
    remove_vehicle(cfg, 99)
    assert len(cfg.vehicles) == 2


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusVehiclesConfig.json", SAMPLE_VEHICLES_CONFIG)

    from app.traderplus.traderplus_vehicles_json import (
        add_vehicle,
        parse_vehicles_config,
        save_vehicles_config,
    )

    cfg = parse_vehicles_config(path)
    v = add_vehicle(cfg, "CivilianSedan", height=2)
    v.vehicle_parts = ["Sedan_Door_Left", "Sedan_Door_Right"]
    save_vehicles_config(cfg)

    cfg2 = parse_vehicles_config(path)
    assert len(cfg2.vehicles) == 3
    assert cfg2.vehicles[2].vehicle_name == "CivilianSedan"
    assert cfg2.vehicles[2].vehicle_parts == ["Sedan_Door_Left", "Sedan_Door_Right"]
    # Original data preserved
    assert cfg2.vehicles[0].vehicle_name == "OffroadHatchback"
    assert cfg2.vehicles[0].height == 3
