"""Tests for cli.traderplus_banking_json (TraderPlusBankingConfig parser)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_json

SAMPLE_BANKING_CONFIG = {
    "Version": "2.5",
    "BankingLogs": 1,
    "IsCreditCarNeededForTransaction": 0,
    "TransactionFees": 2.5,
    "DefaultStartCurrency": 1000,
    "DefaultMaxCurrency": 50000,
    "CurrenciesAccepted": [
        "TraderPlus_Money_Dollar1000",
        "TraderPlus_Money_Dollar100",
    ],
    "TheAmountHasBeenTransferedToTheAccount": "Transfer complete!",
}


def test_parse_banking_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusBankingConfig.json", SAMPLE_BANKING_CONFIG)

    from app.traderplus.traderplus_banking_json import parse_banking_config

    cfg = parse_banking_config(path)

    assert cfg.version == "2.5"
    assert cfg.banking_logs == 1
    assert cfg.is_credit_car_needed == 0
    assert cfg.transaction_fees == 2.5
    assert cfg.default_start_currency == 1000
    assert cfg.default_max_currency == 50000
    assert len(cfg.currencies_accepted) == 2
    assert cfg.transfer_message == "Transfer complete!"


def test_banking_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusBankingConfig.json", SAMPLE_BANKING_CONFIG)

    from app.traderplus.traderplus_banking_json import (
        add_accepted_currency,
        parse_banking_config,
        remove_accepted_currency,
    )

    cfg = parse_banking_config(path)

    # add_accepted_currency
    add_accepted_currency(cfg, "TraderPlus_Money_Dollar10")
    assert len(cfg.currencies_accepted) == 3
    assert cfg.currencies_accepted[-1] == "TraderPlus_Money_Dollar10"

    # remove_accepted_currency
    remove_accepted_currency(cfg, 2)
    assert len(cfg.currencies_accepted) == 2

    # out-of-bounds remove is a no-op
    remove_accepted_currency(cfg, 99)
    assert len(cfg.currencies_accepted) == 2


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusBankingConfig.json", SAMPLE_BANKING_CONFIG)

    from app.traderplus.traderplus_banking_json import parse_banking_config, save_banking_config

    cfg = parse_banking_config(path)
    cfg.transaction_fees = 5.0
    cfg.default_max_currency = 999999
    save_banking_config(cfg)

    cfg2 = parse_banking_config(path)
    assert cfg2.transaction_fees == 5.0
    assert cfg2.default_max_currency == 999999
    # Other fields preserved
    assert cfg2.banking_logs == 1
    assert cfg2.transfer_message == "Transfer complete!"
