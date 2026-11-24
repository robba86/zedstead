"""Tests for cli.traderplus_insurance_json (TraderPlusInsuranceConfig parser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import write_json

SAMPLE_INSURANCE_CONFIG = {
    "Version": "2.5",
    "AuthorizedIDInsurance": [1, 2],
    "Insurances": [
        {
            "VehicleName": "OffroadHatchback",
            "InsurancePriceCoefficient": 1.5,
            "CollateralMoneyCoefficient": 0.6,
        },
        {
            "VehicleName": "Sedan_02",
            "InsurancePriceCoefficient": 1.25,
            "CollateralMoneyCoefficient": 0.75,
        },
    ],
}


def test_parse_insurance_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusInsuranceConfig.json", SAMPLE_INSURANCE_CONFIG)

    from app.traderplus.traderplus_insurance_json import parse_insurance_config

    cfg = parse_insurance_config(path)

    assert cfg.version == "2.5"
    assert cfg.authorized_id_insurance == [1, 2]
    assert len(cfg.insurances) == 2

    ins = cfg.insurances[0]
    assert ins.vehicle_name == "OffroadHatchback"
    assert ins.insurance_price_coefficient == 1.5
    assert ins.collateral_money_coefficient == 0.6


def test_insurance_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusInsuranceConfig.json", SAMPLE_INSURANCE_CONFIG)

    from app.traderplus.traderplus_insurance_json import (
        add_authorized_id,
        add_insurance,
        parse_insurance_config,
        remove_authorized_id,
        remove_insurance,
        update_insurance,
    )

    cfg = parse_insurance_config(path)

    # add_insurance
    entry = add_insurance(cfg, "CivilianSedan", price_coeff=2.0, collateral_coeff=0.5)
    assert len(cfg.insurances) == 3
    assert entry.vehicle_name == "CivilianSedan"

    # update_insurance
    update_insurance(cfg, 2, "Truck_01_Covered", 1.8, 0.4)
    assert cfg.insurances[2].vehicle_name == "Truck_01_Covered"
    assert cfg.insurances[2].insurance_price_coefficient == 1.8

    # remove_insurance
    remove_insurance(cfg, 2)
    assert len(cfg.insurances) == 2

    # add_authorized_id (no duplicates)
    add_authorized_id(cfg, 3)
    add_authorized_id(cfg, 3)
    assert cfg.authorized_id_insurance == [1, 2, 3]

    # remove_authorized_id
    remove_authorized_id(cfg, 3)
    assert cfg.authorized_id_insurance == [1, 2]

    # out-of-bounds remove is a no-op
    remove_insurance(cfg, 99)
    assert len(cfg.insurances) == 2


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusInsuranceConfig.json", SAMPLE_INSURANCE_CONFIG)

    from app.traderplus.traderplus_insurance_json import (
        add_insurance,
        parse_insurance_config,
        save_insurance_config,
    )

    cfg = parse_insurance_config(path)
    add_insurance(cfg, "Bus_Blue", price_coeff=3.0, collateral_coeff=0.3)
    save_insurance_config(cfg)

    cfg2 = parse_insurance_config(path)
    assert len(cfg2.insurances) == 3
    assert cfg2.insurances[2].vehicle_name == "Bus_Blue"
    assert cfg2.insurances[2].insurance_price_coefficient == 3.0
    # Original data preserved
    assert cfg2.authorized_id_insurance == [1, 2]
    assert cfg2.insurances[0].vehicle_name == "OffroadHatchback"


@pytest.mark.parametrize("key, value", [("AuthorizedIDInsurance", "12"), ("Insurances", "Sedan_02")])
def test_text_where_a_list_belongs_is_refused_not_split(tmp_path: Path, key, value) -> None:
    """``"AuthorizedIDInsurance": "12"`` was read as the IDs 1 and 2, and the
    next save wrote ``[1, 2]`` over it."""
    from app.traderplus.traderplus_insurance_json import parse_insurance_config

    path = write_json(tmp_path / "TraderPlusInsuranceConfig.json", {**SAMPLE_INSURANCE_CONFIG, key: value})
    with pytest.raises(ValueError, match=key):
        parse_insurance_config(path)


def test_null_lists_read_as_empty(tmp_path: Path) -> None:
    from app.traderplus.traderplus_insurance_json import parse_insurance_config

    data = {**SAMPLE_INSURANCE_CONFIG, "AuthorizedIDInsurance": None, "Insurances": None}
    cfg = parse_insurance_config(write_json(tmp_path / "TraderPlusInsuranceConfig.json", data))
    assert cfg.authorized_id_insurance == []
    assert cfg.insurances == []
