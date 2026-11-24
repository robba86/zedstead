"""Tests for cli.traderplus_garage_json (TraderPlusGarageConfig parser)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_json

SAMPLE_GARAGE_CONFIG = {
    "Version": "2.5",
    "UseGarageOnlyToTrade": 0,
    "VehicleMustHaveLock": 1,
    "SaveVehicleCargo": 1,
    "SaveVehicleHealth": 0,
    "SaveVehicleFuel": 1,
    "PayWithBankAccount": 0,
    "SavedVehicleInGarageForTradeInHour": 24,
    "MaxVehicleStored": 10,
    "ParkInCost": 500,
    "ParkOutCost": 100,
    "WhitelistedObjects": ["OffroadHatchback", "Sedan_02"],
    "NPCs": [
        {
            "ClassName": "SurvivorM_Peter",
            "Position": [1000.0, 50.0, 2000.0],
            "Orientation": [0.0, 0.0, 0.0],
            "ParkingPosition": [1010.0, 50.0, 2010.0],
            "ParkingOrientation": [90.0, 0.0, 0.0],
            "Clothes": ["TShirt_Green", "Jeans_Blue"],
        },
    ],
}


def test_parse_garage_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGarageConfig.json", SAMPLE_GARAGE_CONFIG)

    from app.traderplus.traderplus_garage_json import parse_garage_config

    cfg = parse_garage_config(path)

    assert cfg.version == "2.5"
    # Toggles
    assert cfg.use_garage_only_to_trade == 0
    assert cfg.vehicle_must_have_lock == 1
    assert cfg.save_vehicle_cargo == 1
    assert cfg.save_vehicle_health == 0
    assert cfg.save_vehicle_fuel == 1
    assert cfg.pay_with_bank_account == 0
    # Scalars
    assert cfg.saved_vehicle_in_garage_for_trade_in_hour == 24
    assert cfg.max_vehicle_stored == 10
    assert cfg.park_in_cost == 500
    assert cfg.park_out_cost == 100
    # Lists
    assert cfg.whitelisted_objects == ["OffroadHatchback", "Sedan_02"]
    # NPCs
    assert len(cfg.npcs) == 1
    npc = cfg.npcs[0]
    assert npc.class_name == "SurvivorM_Peter"
    assert npc.position == [1000.0, 50.0, 2000.0]
    assert npc.parking_position == [1010.0, 50.0, 2010.0]
    assert npc.parking_orientation == [90.0, 0.0, 0.0]
    assert npc.clothes == ["TShirt_Green", "Jeans_Blue"]


def test_garage_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGarageConfig.json", SAMPLE_GARAGE_CONFIG)

    from app.traderplus.traderplus_garage_json import (
        add_garage_npc,
        add_whitelisted_object,
        parse_garage_config,
        remove_garage_npc,
        remove_whitelisted_object,
        update_garage_npc,
    )

    cfg = parse_garage_config(path)

    # add_garage_npc
    npc = add_garage_npc(cfg, "SurvivorF_Linda")
    assert len(cfg.npcs) == 2
    assert npc.class_name == "SurvivorF_Linda"

    # update_garage_npc
    update_garage_npc(cfg, 1, position=[500.0, 10.0, 500.0], clothes=["DressShoes_Black"])
    assert cfg.npcs[1].position == [500.0, 10.0, 500.0]
    assert cfg.npcs[1].clothes == ["DressShoes_Black"]

    # remove_garage_npc
    remove_garage_npc(cfg, 1)
    assert len(cfg.npcs) == 1

    # add_whitelisted_object (no duplicates)
    add_whitelisted_object(cfg, "Truck_01_Covered")
    assert "Truck_01_Covered" in cfg.whitelisted_objects
    add_whitelisted_object(cfg, "Truck_01_Covered")
    assert cfg.whitelisted_objects.count("Truck_01_Covered") == 1

    # remove_whitelisted_object
    remove_whitelisted_object(cfg, len(cfg.whitelisted_objects) - 1)
    assert "Truck_01_Covered" not in cfg.whitelisted_objects

    # out-of-bounds operations are no-ops
    remove_garage_npc(cfg, 99)
    assert len(cfg.npcs) == 1
    remove_whitelisted_object(cfg, 99)
    assert len(cfg.whitelisted_objects) == 2


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGarageConfig.json", SAMPLE_GARAGE_CONFIG)

    from app.traderplus.traderplus_garage_json import (
        add_garage_npc,
        parse_garage_config,
        save_garage_config,
    )

    cfg = parse_garage_config(path)
    cfg.max_vehicle_stored = 20
    cfg.save_vehicle_health = 1
    npc = add_garage_npc(cfg)
    npc.position = [3000.0, 100.0, 4000.0]
    save_garage_config(cfg)

    cfg2 = parse_garage_config(path)
    assert cfg2.max_vehicle_stored == 20
    assert cfg2.save_vehicle_health == 1
    assert len(cfg2.npcs) == 2
    assert cfg2.npcs[1].position == [3000.0, 100.0, 4000.0]
    # Original data preserved
    assert cfg2.vehicle_must_have_lock == 1
    assert cfg2.park_in_cost == 500
    assert cfg2.npcs[0].class_name == "SurvivorM_Peter"
