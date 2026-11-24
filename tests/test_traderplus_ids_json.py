"""Tests for cli.traderplus_ids_json (TraderPlusIDsConfig parser)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_json

SAMPLE_IDS_CONFIG = {
    "Version": "2.5",
    "IDs": [
        {
            "Id": 1,
            "Categories": ["Weapons", "Ammo"],
            "LicencesRequired": ["WeaponLicence"],
            "CurrenciesAccepted": ["TraderPlus_Money_Dollar1000"],
        },
        {
            "Id": 2,
            "Categories": ["Food"],
            "LicencesRequired": [],
            "CurrenciesAccepted": [],
        },
    ],
}


def test_parse_ids_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusIDsConfig.json", SAMPLE_IDS_CONFIG)

    from app.traderplus.traderplus_ids_json import parse_ids_config

    cfg = parse_ids_config(path)

    assert cfg.version == "2.5"
    assert len(cfg.entries) == 2

    e1 = cfg.entries[0]
    assert e1.id == 1
    assert e1.categories == ["Weapons", "Ammo"]
    assert e1.licences_required == ["WeaponLicence"]
    assert e1.currencies_accepted == ["TraderPlus_Money_Dollar1000"]

    e2 = cfg.entries[1]
    assert e2.id == 2
    assert e2.categories == ["Food"]


def test_ids_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusIDsConfig.json", SAMPLE_IDS_CONFIG)

    from app.traderplus.traderplus_ids_json import (
        add_category,
        add_entry,
        add_licence_required,
        find_entry_by_id,
        parse_ids_config,
        remove_category,
        remove_entry,
        remove_licence_required,
        set_categories,
        set_currencies_accepted,
    )

    cfg = parse_ids_config(path)

    # add_entry
    entry = add_entry(cfg, 3)
    assert len(cfg.entries) == 3
    assert entry.id == 3

    # find_entry_by_id
    assert find_entry_by_id(cfg, 3) == 2
    assert find_entry_by_id(cfg, 999) is None

    # add_category (no duplicates)
    add_category(entry, "Medical")
    add_category(entry, "Medical")
    assert entry.categories == ["Medical"]

    # remove_category
    remove_category(entry, "Medical")
    assert entry.categories == []

    # set_categories
    set_categories(entry, ["Vehicles", "Parts"])
    assert entry.categories == ["Vehicles", "Parts"]

    # add/remove licence_required
    add_licence_required(entry, "VehicleLicence")
    add_licence_required(entry, "VehicleLicence")  # no duplicate
    assert entry.licences_required == ["VehicleLicence"]
    remove_licence_required(entry, "VehicleLicence")
    assert entry.licences_required == []

    # set_currencies_accepted
    set_currencies_accepted(entry, ["Dollar", "Euro"])
    assert entry.currencies_accepted == ["Dollar", "Euro"]

    # remove_entry
    remove_entry(cfg, 2)
    assert len(cfg.entries) == 2


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusIDsConfig.json", SAMPLE_IDS_CONFIG)

    from app.traderplus.traderplus_ids_json import add_entry, parse_ids_config, save_ids_config

    cfg = parse_ids_config(path)
    new_entry = add_entry(cfg, 5)
    new_entry.categories = ["Clothing"]
    save_ids_config(cfg)

    cfg2 = parse_ids_config(path)
    assert len(cfg2.entries) == 3
    assert cfg2.entries[2].id == 5
    assert cfg2.entries[2].categories == ["Clothing"]
    # Original entries preserved
    assert cfg2.entries[0].id == 1
    assert cfg2.entries[0].categories == ["Weapons", "Ammo"]
