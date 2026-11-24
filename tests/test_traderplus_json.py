"""Tests for cli.traderplus_json (TraderPlusPriceConfig parser)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from tests.conftest import write_json

SAMPLE_PRICE_CONFIG = {
    "Version": "2.5",
    "EnableAutoCalculation": 0,
    "EnableAutoDestockAtRestart": 1,
    "EnableDefaultTraderStock": 0,
    "TraderCategories": [
        {
            "CategoryName": "Weapons",
            "Products": [
                "M4A1,0.4,-2,1,10000,5000",
                "AKM,0.5,10,1,8000,4000,0.9",
            ],
        },
        {
            "CategoryName": "Food",
            "Products": [
                "Apple,1,100,1,10,5",
            ],
        },
    ],
}


def test_parse_price_config(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusPriceConfig.json", SAMPLE_PRICE_CONFIG)

    from app.traderplus.traderplus_json import parse_price_config

    cfg = parse_price_config(path)

    assert cfg.version == "2.5"
    assert cfg.enable_auto_calculation == 0
    assert cfg.enable_auto_destock == 1
    assert cfg.enable_default_stock == 0
    assert len(cfg.categories) == 2

    weapons = cfg.categories[0]
    assert weapons.name == "Weapons"
    assert len(weapons.products) == 2

    m4 = weapons.products[0]
    assert m4.classname == "M4A1"
    assert m4.coefficient == 0.4
    assert m4.max_stock == -2
    assert m4.trade_quantity == 1.0
    assert m4.buy_price == 10000.0
    assert m4.sell_price == 5000.0
    assert m4.destock_coefficient is None

    akm = weapons.products[1]
    assert akm.classname == "AKM"
    assert akm.destock_coefficient == 0.9


def test_price_config_crud(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusPriceConfig.json", SAMPLE_PRICE_CONFIG)

    from app.traderplus.traderplus_json import (
        TraderPlusProduct,
        add_category,
        add_product,
        find_product,
        parse_price_config,
        remove_category,
        remove_product,
        rename_category,
        update_product,
    )

    cfg = parse_price_config(path)

    # add_category
    cat = add_category(cfg, "Ammo")
    assert len(cfg.categories) == 3
    assert cat.name == "Ammo"

    # rename_category
    rename_category(cfg, 2, "Ammunition")
    assert cfg.categories[2].name == "Ammunition"

    # add_product
    bullet = TraderPlusProduct(classname="Bullet_556x45", buy_price=50, sell_price=25)
    add_product(cfg.categories[2], bullet)
    assert len(cfg.categories[2].products) == 1

    # update_product
    new_bullet = TraderPlusProduct(classname="Bullet_762x39", buy_price=60, sell_price=30)
    update_product(cfg.categories[2], 0, new_bullet)
    assert cfg.categories[2].products[0].classname == "Bullet_762x39"

    # remove_product
    remove_product(cfg.categories[2], 0)
    assert len(cfg.categories[2].products) == 0

    # remove_category
    remove_category(cfg, 2)
    assert len(cfg.categories) == 2

    # find_product (case-insensitive)
    results = find_product(cfg, "m4a1")
    assert len(results) == 1
    assert results[0][2].classname == "M4A1"


def test_move_product_and_category() -> None:
    from app.traderplus.traderplus_json import (
        TraderPlusCategory, TraderPlusPriceConfig, TraderPlusProduct,
        move_category, move_product,
    )

    cat = TraderPlusCategory(name="Food", products=[
        TraderPlusProduct(classname="A"),
        TraderPlusProduct(classname="B"),
        TraderPlusProduct(classname="C"),
    ])

    # Move B (idx 1) up → swaps with A → new idx 0
    new_idx = move_product(cat, 1, -1)
    assert new_idx == 0
    assert [p.classname for p in cat.products] == ["B", "A", "C"]

    # Move B (now idx 0) up again → at edge, stays put
    new_idx = move_product(cat, 0, -1)
    assert new_idx == 0
    assert [p.classname for p in cat.products] == ["B", "A", "C"]

    # Move A (idx 1) down → swaps with C → new idx 2
    new_idx = move_product(cat, 1, 1)
    assert new_idx == 2
    assert [p.classname for p in cat.products] == ["B", "C", "A"]

    cfg = TraderPlusPriceConfig(categories=[
        TraderPlusCategory(name="X"),
        TraderPlusCategory(name="Y"),
    ])
    assert move_category(cfg, 0, 1) == 1
    assert [c.name for c in cfg.categories] == ["Y", "X"]
    # At edge — no change.
    assert move_category(cfg, 1, 1) == 1
    assert [c.name for c in cfg.categories] == ["Y", "X"]


def test_apply_batch_set_skips_disabled_prices_and_int_casts() -> None:
    from app.traderplus.traderplus_json import (
        TraderPlusCategory, TraderPlusProduct, apply_batch_set,
    )

    cat = TraderPlusCategory(name="Food", products=[
        TraderPlusProduct(classname="A", buy_price=10, sell_price=5, max_stock=10),
        TraderPlusProduct(classname="B", buy_price=-1, sell_price=-1, max_stock=20),  # disabled
        TraderPlusProduct(classname="C", buy_price=20, sell_price=10, max_stock=5),
    ])

    n = apply_batch_set(cat, "buy_price", 99)
    assert n == 2  # B is skipped because buy_price == -1
    assert cat.products[0].buy_price == 99.0
    assert cat.products[1].buy_price == -1
    assert cat.products[2].buy_price == 99.0

    # max_stock is cast to int
    apply_batch_set(cat, "max_stock", 50.7)
    assert cat.products[0].max_stock == 51
    assert isinstance(cat.products[0].max_stock, int)


def test_apply_category_multiplier_columns_argument() -> None:
    from app.traderplus.traderplus_json import (
        TraderPlusCategory, TraderPlusPriceConfig, TraderPlusProduct,
        apply_category_multiplier,
    )

    cfg = TraderPlusPriceConfig(categories=[
        TraderPlusCategory(name="Food", products=[
            TraderPlusProduct(
                classname="A", buy_price=10, sell_price=5,
                coefficient=1.0, max_stock=10, trade_quantity=2,
            ),
        ]),
    ])

    # Default behaviour (no `columns`) scales buy + sell only.
    changes = apply_category_multiplier(cfg, 0, 2.0)
    assert {c[2] for c in changes} == {"buy_price", "sell_price"}
    assert cfg.categories[0].products[0].coefficient == 1.0  # untouched
    assert cfg.categories[0].products[0].trade_quantity == 2  # untouched

    # Reset and request only coefficient.
    cfg.categories[0].products[0].buy_price = 10
    cfg.categories[0].products[0].sell_price = 5
    changes = apply_category_multiplier(cfg, 0, 3.0, columns={"coefficient"})
    assert all(c[2] == "coefficient" for c in changes)
    assert cfg.categories[0].products[0].coefficient == 3.0
    # buy/sell unchanged this time
    assert cfg.categories[0].products[0].buy_price == 10
    assert cfg.categories[0].products[0].sell_price == 5


@pytest.mark.parametrize(
    "multiplier, columns, expected",
    [
        (0.5, {"buy_price"}, (50, 50)),   # the stored sell price follows the lowered buy price
        (2.0, {"sell_price"}, (100, 100)),  # a scaled sell price stops at the stored buy price
        (2.0, {"buy_price"}, (200, 80)),
        (0.5, {"sell_price"}, (100, 40)),
    ],
)
def test_a_multiplier_on_one_price_column_keeps_sell_at_or_below_buy(multiplier, columns, expected) -> None:
    """The clamp ran against a buy price that was never saved: 0.5 on buy gave
    buy 50, sell 80, money from nothing."""
    from app.traderplus.traderplus_json import (
        TraderPlusCategory, TraderPlusPriceConfig, TraderPlusProduct,
        apply_category_multiplier,
    )

    product = TraderPlusProduct.from_string("M4A1,1,-1,1,100,80")
    cfg = TraderPlusPriceConfig(categories=[TraderPlusCategory(name="Weapons", products=[product])])

    apply_category_multiplier(cfg, 0, multiplier, columns=columns)

    assert (product.buy_price, product.sell_price) == expected


def test_round_trip(tmp_path: Path) -> None:
    path = write_json(tmp_path / "TraderPlusPriceConfig.json", SAMPLE_PRICE_CONFIG)

    from app.traderplus.traderplus_json import (
        add_category,
        parse_price_config,
        save_price_config,
    )

    cfg = parse_price_config(path)
    add_category(cfg, "Medical")
    save_price_config(cfg)

    cfg2 = parse_price_config(path)
    assert len(cfg2.categories) == 3
    assert cfg2.categories[2].name == "Medical"
    assert cfg2.enable_auto_destock == 1

    # Verify original products survived round-trip
    m4 = cfg2.categories[0].products[0]
    assert m4.classname == "M4A1"
    assert m4.buy_price == 10000.0


def test_product_serializes_without_scientific_notation() -> None:
    """Large/fractional prices must serialize as plain decimals.

    Regression: _fmt_num used %g, which emits scientific notation past 6
    significant figures (2500000.5 -> "2.5e+06", 1000000.5 -> "1e+06") —
    TraderPlus's JSON parser can't read that, and it silently dropped the
    fractional part too.
    """
    from app.traderplus.traderplus_json import TraderPlusProduct

    s = "Heli,1,1,1,2500000.5,1875000.25,0.5"
    out = TraderPlusProduct.from_string(s).to_string()
    assert "e+" not in out and "E+" not in out
    assert out == s  # exact round-trip, fractional millions intact

    # Whole values stay integer-formatted, small fractions stay decimal.
    p = TraderPlusProduct(
        classname="X", coefficient=0.00001, max_stock=-1,
        trade_quantity=1, buy_price=1234567.5, sell_price=-1,
    )
    out2 = p.to_string()
    assert "e-" not in out2 and "e+" not in out2
    assert out2 == "X,0.00001,-1,1,1234567.5,-1"


UNREADABLE_ENTRIES = [
    "AKM,1,-1,1,100",  # no sell price: saving used to invent one
    "AKM",
    "AKM,1,-1,1,,50",
    "AKM,1,-1,1,100,fifty",
    "AKM,1,inf,1,100,50",
    "AKM,1,-1,1,nan,50",
    "AKM,1,-1,1,1e400,50",
    "AKM,1,-1,1,100,50,x",
    "AKM,1,-1,1,100,50,inf",
    5,
    None,
    ["AKM", 1],
]


@pytest.mark.parametrize("entry", UNREADABLE_ENTRIES, ids=repr)
def test_a_line_that_cannot_be_read_is_kept_as_it_is(entry) -> None:
    from app.traderplus.traderplus_json import TraderPlusProduct

    product = TraderPlusProduct.from_string(entry)

    assert product.unreadable
    assert isinstance(product.classname, str)
    assert product.raw == entry and type(product.raw) is type(entry)
    assert product.to_entry() == entry and type(product.to_entry()) is type(entry)


def test_a_readable_line_is_not_marked() -> None:
    from app.traderplus.traderplus_json import TraderPlusProduct

    product = TraderPlusProduct.from_string("AKM,0.5,10,1,8000,4000,0.9")

    assert not product.unreadable
    assert product.to_entry() == "AKM,0.5,10,1,8000,4000,0.9"


def test_unreadable_lines_survive_parse_and_save(tmp_path: Path) -> None:
    from app.traderplus.traderplus_json import parse_price_config, save_price_config

    categories = [
        {"CategoryName": "Weapons", "Products": ["M4A1,0.4,-2,1,10000,5000", *UNREADABLE_ENTRIES]},
        {"CategoryName": "Food", "Products": [None, "Apple,1,100,1,10,5"]},
    ]
    path = write_json(tmp_path / "TraderPlusPriceConfig.json", {**SAMPLE_PRICE_CONFIG, "TraderCategories": categories})

    cfg = parse_price_config(path)
    save_price_config(cfg)

    assert [p.unreadable for p in cfg.categories[1].products] == [True, False]
    assert json.loads(path.read_text(encoding="utf-8"))["TraderCategories"] == categories


def test_bulk_edits_skip_a_line_that_cannot_be_read() -> None:
    from app.traderplus.traderplus_json import (
        TraderPlusCategory,
        TraderPlusPriceConfig,
        TraderPlusProduct,
        apply_batch_set,
        apply_category_multiplier,
    )

    good = TraderPlusProduct.from_string("M4A1,1,10,1,100,50")
    bad = TraderPlusProduct.from_string("AKM,1,-1,1,100")
    before = dataclasses.replace(bad)
    cat = TraderPlusCategory(name="Weapons", products=[good, bad])
    cfg = TraderPlusPriceConfig(categories=[cat])

    changes = apply_category_multiplier(
        cfg, None, 2.0, columns={"buy_price", "sell_price", "coefficient", "trade_quantity"},
    )

    assert {change[0] for change in changes} == {"M4A1"}
    assert apply_batch_set(cat, "coefficient", 3.0) == 1
    assert bad == before
    assert bad.to_entry() == "AKM,1,-1,1,100"


@pytest.mark.parametrize("products", ["Coins", {"a": 1}])
def test_products_that_are_not_a_list_are_refused_not_split(tmp_path: Path, products) -> None:
    """``Products`` holding text was read one letter per product, and the next
    save wrote ``["C", "o", "i", "n", "s"]`` over it."""
    from app.traderplus.traderplus_json import parse_price_config

    categories = [{"CategoryName": "Weapons", "Products": products}]
    path = write_json(tmp_path / "TraderPlusPriceConfig.json", {**SAMPLE_PRICE_CONFIG, "TraderCategories": categories})

    with pytest.raises(ValueError, match="Products"):
        parse_price_config(path)


def test_null_categories_and_products_read_as_empty(tmp_path: Path) -> None:
    from app.traderplus.traderplus_json import parse_price_config

    path = write_json(tmp_path / "TraderPlusPriceConfig.json", {**SAMPLE_PRICE_CONFIG, "TraderCategories": None})
    assert parse_price_config(path).categories == []

    categories = [{"CategoryName": "Weapons", "Products": None}]
    path = write_json(tmp_path / "TraderPlusPriceConfig.json", {**SAMPLE_PRICE_CONFIG, "TraderCategories": categories})
    assert parse_price_config(path).categories[0].products == []
