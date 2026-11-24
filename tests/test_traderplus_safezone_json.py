"""Tests for cli.traderplus_safezone_json (TraderPlusSafeZoneConfig parser)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_json

SAMPLE_SAFEZONE_CONFIG = {
    "Version": "2.5",
    "IsHideOutActive": 1,
    "EnableAfkDisconnect": 1,
    "KickAfterDelay": 60,
    "CleanUpTimer": 300,
    "MsgEnterZone": "Welcome to the safe zone!",
    "MsgExitZone": "You left the safe zone.",
    "MsgOnLeavingZone": "Leaving zone in {0}s",
    "MustRemoveArmband": "Remove your armband!",
    "SafeAreaLocation": [
        {
            "SafeZoneStatut": "TraderZone1",
            "X": 1234.5,
            "Y": 6789.0,
            "Radius": 100,
            "Countdown": 20,
        },
    ],
    "WhitelistEntities": ["Land_City_FireStation"],
    "ObjectsToDelete": ["BearTrap"],
    "SZSteamUIDs": ["76561198000000001"],
    "BlackListedItemInStash": ["LAR"],
}


def test_parse_safezone_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusSafeZoneConfig.json", SAMPLE_SAFEZONE_CONFIG)

    from app.traderplus.traderplus_safezone_json import parse_safezone_config

    cfg = parse_safezone_config(path)

    assert cfg.version == "2.5"
    assert cfg.is_hideout_active == 1
    assert cfg.enable_afk_disconnect == 1
    assert cfg.kick_after_delay == 60
    assert cfg.cleanup_timer == 300
    assert cfg.msg_enter_zone == "Welcome to the safe zone!"
    assert cfg.msg_exit_zone == "You left the safe zone."

    assert len(cfg.safe_area_locations) == 1
    area = cfg.safe_area_locations[0]
    assert area.safe_zone_statut == "TraderZone1"
    assert area.x == 1234.5
    assert area.y == 6789.0
    assert area.radius == 100
    assert area.countdown == 20

    assert cfg.whitelist_entities == ["Land_City_FireStation"]
    assert cfg.objects_to_delete == ["BearTrap"]
    assert cfg.sz_steam_uids == ["76561198000000001"]
    assert cfg.blacklisted_item_in_stash == ["LAR"]


def test_safezone_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusSafeZoneConfig.json", SAMPLE_SAFEZONE_CONFIG)

    from app.traderplus.traderplus_safezone_json import (
        add_safe_area,
        add_to_list,
        parse_safezone_config,
        remove_from_list,
        remove_safe_area,
        update_safe_area,
    )

    cfg = parse_safezone_config(path)

    # add_safe_area
    area = add_safe_area(cfg, "TraderZone2", x=5000.0, y=5000.0, radius=120, countdown=10)
    assert len(cfg.safe_area_locations) == 2
    assert area.safe_zone_statut == "TraderZone2"
    assert area.radius == 120

    # update_safe_area (partial update)
    update_safe_area(cfg, 1, radius=200)
    assert cfg.safe_area_locations[1].radius == 200
    assert cfg.safe_area_locations[1].x == 5000.0  # unchanged

    update_safe_area(cfg, 1, name="TraderZone2_Updated", x=5500.0)
    assert cfg.safe_area_locations[1].safe_zone_statut == "TraderZone2_Updated"
    assert cfg.safe_area_locations[1].x == 5500.0

    # remove_safe_area
    remove_safe_area(cfg, 1)
    assert len(cfg.safe_area_locations) == 1

    # add_to_list / remove_from_list (generic string list helpers)
    add_to_list(cfg.whitelist_entities, "Fence")
    assert "Fence" in cfg.whitelist_entities
    add_to_list(cfg.whitelist_entities, "Fence")  # no duplicate
    assert cfg.whitelist_entities.count("Fence") == 1

    remove_from_list(cfg.whitelist_entities, len(cfg.whitelist_entities) - 1)
    assert "Fence" not in cfg.whitelist_entities

    # out-of-bounds operations are no-ops
    remove_safe_area(cfg, 99)
    assert len(cfg.safe_area_locations) == 1
    remove_from_list(cfg.objects_to_delete, 99)
    assert len(cfg.objects_to_delete) == 1


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusSafeZoneConfig.json", SAMPLE_SAFEZONE_CONFIG)

    from app.traderplus.traderplus_safezone_json import (
        add_safe_area,
        parse_safezone_config,
        save_safezone_config,
    )

    cfg = parse_safezone_config(path)
    cfg.kick_after_delay = 120
    add_safe_area(cfg, "NewZone", x=100.0, y=200.0)
    save_safezone_config(cfg)

    cfg2 = parse_safezone_config(path)
    assert cfg2.kick_after_delay == 120
    assert len(cfg2.safe_area_locations) == 2
    assert cfg2.safe_area_locations[1].safe_zone_statut == "NewZone"
    assert cfg2.safe_area_locations[1].x == 100.0
    # Original data preserved
    assert cfg2.msg_enter_zone == "Welcome to the safe zone!"
    assert cfg2.whitelist_entities == ["Land_City_FireStation"]
