"""Tests for cli.traderplus_general_json (TraderPlusGeneralConfig parser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import write_json

SAMPLE_GENERAL_CONFIG = {
    "Version": "2.5",
    "ConvertTraderConfigToTraderPlus": 0,
    "ConvertTraderConfigToTraderPlusWithStockBasedOnCE": 0,
    "UseGarageToTradeCar": 1,
    "DisableHeightFailSafeForReceiptDeployment": 0,
    "HideInsuranceBtn": 0,
    "HideGarageBtn": 0,
    "HideLicenceBtn": 1,
    "EnableShowAllPrices": 1,
    "EnableShowAllCheckBox": 0,
    "IsReceiptTraderOnly": 0,
    "IsReceiptSaveLock": 0,
    "IsReceiptSaveAttachment": 0,
    "IsReceiptSaveCargo": 0,
    "StoreOnlyToPristineState": 0,
    "LockPickChance": 0.15,
    "LicenceKeyWord": "Licence",
    "Licences": ["WeaponLicence", "VehicleLicence"],
    "AcceptedStates": {
        "AcceptWorn": 1,
        "AcceptDamaged": 1,
        "AcceptBadlyDamaged": 0,
        "CoefficientWorn": 0.75,
        "CoefficientDamaged": 0.5,
        "CoefficientBadlyDamaged": 0.25,
    },
    "Currencies": [
        {"ClassName": "TraderPlus_Money_Dollar1000", "Value": 1000},
        {"ClassName": "TraderPlus_Money_Dollar100", "Value": 100},
    ],
    "Traders": [{"Id": 1, "Name": "Bob"}],
    "TraderObjects": [{"ObjectName": "Land_House"}],
}


def test_parse_general_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGeneralConfig.json", SAMPLE_GENERAL_CONFIG)

    from app.traderplus.traderplus_general_json import parse_general_config

    cfg = parse_general_config(path)

    assert cfg.version == "2.5"
    assert cfg.use_garage_to_trade_car == 1
    assert cfg.hide_licence_btn == 1
    assert cfg.enable_show_all_prices == 1
    assert cfg.lock_pick_chance == 0.15
    assert cfg.licence_keyword == "Licence"
    assert cfg.licences == ["WeaponLicence", "VehicleLicence"]

    # AcceptedStates
    assert cfg.accepted_states.accept_badly_damaged == 0
    assert cfg.accepted_states.coefficient_worn == 0.75

    # Currencies
    assert len(cfg.currencies) == 2
    assert cfg.currencies[0].classname == "TraderPlus_Money_Dollar1000"
    assert cfg.currencies[0].value == 1000

    # Traders
    assert len(cfg.traders) == 1
    assert cfg.traders[0].id == 1
    assert cfg.traders[0].name == "Bob"
    assert cfg.trader_object_count == 1


def test_general_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGeneralConfig.json", SAMPLE_GENERAL_CONFIG)

    from app.traderplus.traderplus_general_json import (
        add_currency,
        add_licence,
        parse_general_config,
        remove_currency,
        remove_licence,
        update_currency,
    )

    cfg = parse_general_config(path)

    # add_currency (inserted in descending value order)
    add_currency(cfg, "TraderPlus_Money_Dollar500", 500)
    assert len(cfg.currencies) == 3
    # 1000, 500, 100 order
    assert cfg.currencies[1].classname == "TraderPlus_Money_Dollar500"

    # update_currency
    update_currency(cfg, 1, "TraderPlus_Money_Dollar250", 250)
    assert cfg.currencies[1].classname == "TraderPlus_Money_Dollar250"
    assert cfg.currencies[1].value == 250

    # remove_currency
    remove_currency(cfg, 1)
    assert len(cfg.currencies) == 2

    # add_licence
    add_licence(cfg, "MedicalLicence")
    assert "MedicalLicence" in cfg.licences

    # remove_licence
    remove_licence(cfg, len(cfg.licences) - 1)
    assert "MedicalLicence" not in cfg.licences


def test_trader_npc_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGeneralConfig.json", SAMPLE_GENERAL_CONFIG)

    from app.traderplus.traderplus_general_json import (
        add_trader_npc,
        parse_general_config,
        remove_trader_npc,
        save_general_config,
        update_trader_npc,
    )

    cfg = parse_general_config(path)
    assert len(cfg.traders) == 1

    # add
    npc = add_trader_npc(cfg, id=5, given_name="Marie", role="Food Trader", name="SurvivorF_Linda")
    assert npc.id == 5
    assert len(cfg.traders) == 2

    # update
    update_trader_npc(cfg, 1, given_name="Anna", clothes=["Raincoat_Blue"])
    assert cfg.traders[1].given_name == "Anna"
    assert cfg.traders[1].clothes == ["Raincoat_Blue"]

    # round-trip
    save_general_config(cfg)
    cfg2 = parse_general_config(path)
    assert len(cfg2.traders) == 2
    assert cfg2.traders[1].given_name == "Anna"
    assert cfg2.traders[1].clothes == ["Raincoat_Blue"]

    # remove
    remove_trader_npc(cfg2, 1)
    assert len(cfg2.traders) == 1


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusGeneralConfig.json", SAMPLE_GENERAL_CONFIG)

    from app.traderplus.traderplus_general_json import parse_general_config, save_general_config

    cfg = parse_general_config(path)
    cfg.lock_pick_chance = 0.5
    cfg.enable_show_all_prices = 0
    save_general_config(cfg)

    cfg2 = parse_general_config(path)
    assert cfg2.lock_pick_chance == 0.5
    assert cfg2.enable_show_all_prices == 0
    # Traders preserved
    assert len(cfg2.traders) == 1
    assert cfg2.traders[0].name == "Bob"
    assert cfg2.licence_keyword == "Licence"


def test_text_in_a_trader_s_list_is_refused_not_split_into_letters(tmp_path: Path) -> None:
    from app.traderplus.traderplus_general_json import parse_general_config

    doc = dict(SAMPLE_GENERAL_CONFIG, Traders=[{"Id": 1, "Name": "Bob"}, {"Id": 2, "Clothes": "Hat"}])
    path = write_json(tmp_path / "TraderPlusGeneralConfig.json", doc)

    with pytest.raises(ValueError, match=r'TraderPlusGeneralConfig\.json, trader 2: "Clothes" holds text'):
        parse_general_config(path)
