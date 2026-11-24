"""A TraderPlus save adds no key the file lacks unless that value was set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.traderplus import (
    traderplus_banking_json as banking,
    traderplus_garage_json as garage,
    traderplus_general_json as general,
    traderplus_ids_json as ids,
    traderplus_insurance_json as insurance,
    traderplus_json as price,
    traderplus_safezone_json as safezone,
    traderplus_vehicles_json as vehicles,
)

# Each file with most keys left out, and one entry that lacks some of its own.
CASES = [
    (general.parse_general_config, general.save_general_config,
     {"Version": "2.5", "Traders": [{"Id": 1, "Name": "SurvivorM_Peter"}], "AcceptedStates": {"AcceptWorn": 0}}),
    (safezone.parse_safezone_config, safezone.save_safezone_config,
     {"Version": "2.5", "SafeAreaLocation": [{"SafeZoneStatut": "Green", "X": 1.5, "Y": 2.5}]}),
    (garage.parse_garage_config, garage.save_garage_config,
     {"Version": "2.5", "NPCs": [{"ClassName": "SurvivorM_Boris"}]}),
    (banking.parse_banking_config, banking.save_banking_config, {"Version": "2.5"}),
    (ids.parse_ids_config, ids.save_ids_config, {"Version": "2.5"}),
    (vehicles.parse_vehicles_config, vehicles.save_vehicles_config, {"Version": "2.5"}),
    (insurance.parse_insurance_config, insurance.save_insurance_config,
     {"Version": "2.5", "Insurances": [{"VehicleName": "OffroadHatchback"}]}),
    (price.parse_price_config, price.save_price_config,
     {"TraderCategories": [{"CategoryName": "Food"}]}),
]


@pytest.mark.parametrize("parse, save, data", CASES, ids=lambda c: getattr(c, "__name__", "data"))
def test_a_save_with_no_edits_adds_no_key(tmp_path: Path, parse, save, data) -> None:
    path = tmp_path / "TraderPlus.json"
    text = json.dumps(data, indent=4) + "\n"
    path.write_text(text, encoding="utf-8")
    save(parse(path))
    assert path.read_text(encoding="utf-8") == text


def test_a_value_set_for_a_missing_key_is_added(tmp_path: Path) -> None:
    path = tmp_path / "TraderPlusSafeZoneConfig.json"
    path.write_text(json.dumps({"Version": "2.5"}), encoding="utf-8")
    cfg = safezone.parse_safezone_config(path)
    cfg.kick_after_delay = 45
    safezone.save_safezone_config(cfg)
    assert json.loads(path.read_text(encoding="utf-8")) == {"Version": "2.5", "KickAfterDelay": 45}


def test_a_new_entry_gets_every_key(tmp_path: Path) -> None:
    path = tmp_path / "TraderPlusGeneralConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "Currencies": []}), encoding="utf-8")
    cfg = general.parse_general_config(path)
    general.add_currency(cfg, "TraderPlus_Money_Dollar1", 1)
    general.save_general_config(cfg)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"Version": "2.5", "Currencies": [{"ClassName": "TraderPlus_Money_Dollar1", "Value": 1}]}
