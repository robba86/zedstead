"""Tests for app/ops/zone_trader.py — a zone mod's items as a TraderPlus
category (plans/48, package D).

Every class name, trader and price here is our own. The mods' terms forbid
re-use of their files, so nothing in this file comes from them.
"""

from __future__ import annotations

import json

import pytest

from app.ops import log_analysis, zone_trader
from app.traderplus.traderplus_ids_json import parse_ids_config
from app.traderplus.traderplus_json import parse_price_config
from tests.conftest import mods_lines, write_test_mods

RADZONE = "@RadZone"

# A bare fragment with CRLF line ends: the shape both zone mods ship, which
# the root-based classifier does not recognise.
TYPES = (
    '\t<type name="ZoneTestCure">\r\n\t\t<nominal>2</nominal>\r\n\t</type>\r\n'
    '\t<type name="ZoneTestSuit"><nominal>2</nominal></type>\r\n'
    '\t<type name="ZoneTestBase"><nominal>2</nominal></type>\r\n'
    '\t<type name="ZoneTestGhost"><nominal>2</nominal></type>\r\n'
    '\t<type name="ZoneTestCure"><nominal>2</nominal></type>\r\n'
)


def _rpt(mods: str, *rejects: tuple[str, str]) -> str:
    why = {"missing": "Type does not exist. (Typo?)", "scope": "Not spawnable. (Scope is not public?)"}
    lines = [
        "== DayZServer",
        f"== ./DayZServer ./DayZServer -config=serverDZ.cfg -mod={mods} -port=2302 ",
        *(f"10:14:29 !!! [CE][offlineDB] :: Type '{name}' will be ignored. ({why[kind]})"
          for name, kind in rejects),
    ]
    return "\n".join(lines) + "\n"


def _tp_dir(cfg):
    d = cfg.server_dir_path / cfg.profiles_path / "TraderPlus" / "TraderPlusConfig"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def rz(sample_config):
    """An installed RadZone with a types file of our own, and TraderPlus files."""
    cfg = sample_config
    write_test_mods(cfg, mods_lines(f"@CF\n{RADZONE}\n"))
    files = cfg.server_dir_path / RADZONE / "ServerFiles"
    files.mkdir(parents=True)
    (files / "types.xml").write_text(TYPES, encoding="utf-8")
    (files / "notes.xml").write_text("<notes><type name=\"NotAClass\"/></notes>", encoding="utf-8")
    d = _tp_dir(cfg)
    (d / "TraderPlusPriceConfig.json").write_text(json.dumps({
        "Version": "2.5", "EnableAutoCalculation": 0, "EnableAutoDestockAtRestart": 0,
        "EnableDefaultTraderStock": 0,
        "TraderCategories": [{"CategoryName": "Medic", "Products": ["ZoneTestCure,1,-1,1,300,100"]}],
    }), encoding="utf-8")
    (d / "TraderPlusIDsConfig.json").write_text(json.dumps({
        "Version": "2.5",
        "IDs": [
            {"Id": 1, "Categories": ["Medic"], "LicencesRequired": [], "CurrenciesAccepted": []},
            {"Id": 2, "Categories": [], "LicencesRequired": [], "CurrenciesAccepted": []},
        ],
    }), encoding="utf-8")
    (d / "TraderPlusGeneralConfig.json").write_text(json.dumps({
        "Traders": [
            {"Id": 1, "Name": "SurvivorM_Test", "GivenName": "Ada", "Role": "Medic"},
            {"Id": 1, "Name": "SurvivorF_Test", "GivenName": "", "Role": ""},
        ],
    }), encoding="utf-8")
    return cfg


def _boot(cfg, text: str) -> None:
    profiles = cfg.server_dir_path / cfg.profiles_path
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "DayZServer_x64_2026-09-24_10-14-00.RPT").write_text(text, encoding="utf-8")


def _offer(cfg) -> dict:
    return zone_trader.offers(cfg)["offers"][0]


# ── rejected_classes ──────────────────────────────────────────────────


def test_rejected_classes_reads_both_messages_and_the_mod_list(tmp_path):
    log = tmp_path / "x.RPT"
    log.write_text(_rpt("@CF;@RadZone", ("A", "missing"), ("B", "scope")), encoding="utf-8")
    rejected, mods = log_analysis.rejected_classes(log)
    assert rejected == {"A": "ce_type_missing", "B": "ce_type_not_spawnable"}
    assert mods == ["@CF", "@RadZone"]


def test_rejected_classes_without_a_command_line_does_not_know_the_mods(tmp_path):
    log = tmp_path / "x.RPT"
    log.write_text("10:14:29 nothing here\n", encoding="utf-8")
    assert log_analysis.rejected_classes(log) == ({}, None)


# ── offers ───────────────────────────────────────────────────────────


def test_the_offer_lists_the_mods_classes_once_in_file_order(rz):
    offer = _offer(rz)
    assert offer["key"] == "radzone"
    assert offer["category"] == "RadZone"
    assert offer["sources"] == [f"{RADZONE}/ServerFiles/types.xml"]
    # ZoneTestCure is already priced, and the duplicate line adds nothing.
    assert offer["items"] == ["ZoneTestSuit", "ZoneTestBase", "ZoneTestGhost"]
    assert offer["skipped"] == [
        {"classname": "ZoneTestCure", "reason": "already in the price file, in Medic"},
    ]
    assert offer["checked"] is None
    assert "No server log" in offer["check_note"]


def test_classes_the_last_boot_refused_are_left_out_with_the_reason(rz):
    _boot(rz, _rpt("@CF;@RadZone", ("ZoneTestBase", "scope"), ("ZoneTestGhost", "missing")))
    offer = _offer(rz)
    assert offer["items"] == ["ZoneTestSuit"]
    reasons = {s["classname"]: s["reason"] for s in offer["skipped"]}
    assert "base class" in reasons["ZoneTestBase"]
    assert "no class by this name" in reasons["ZoneTestGhost"]
    assert offer["checked"].endswith(".RPT")


def test_a_boot_without_the_mod_checks_nothing(rz):
    # Without the mod every one of its classes "does not exist"; that says
    # nothing about the mod.
    _boot(rz, _rpt("@CF", ("ZoneTestSuit", "missing")))
    offer = _offer(rz)
    assert "ZoneTestSuit" in offer["items"]
    assert offer["checked"] is None
    assert "did not load @RadZone" in offer["check_note"]


def test_the_traders_carry_the_npcs_that_use_each_id(rz):
    traders = zone_trader.offers(rz)["traders"]
    assert traders == [
        {"id": 1, "names": ["Ada (Medic)", "SurvivorF_Test"], "categories": 1},
        {"id": 2, "names": [], "categories": 0},
    ]


def test_no_zone_mod_installed_means_no_offers(sample_config):
    body = zone_trader.offers(sample_config)
    assert body == {"price_file": None, "traders": [], "offers": []}


# ── add_to_traderplus ────────────────────────────────────────────────


def test_adding_writes_an_unpriced_category_and_gives_it_to_the_traders(rz):
    result = zone_trader.add_to_traderplus(rz, "radzone", " Radiation ", [2])
    assert result["category"] == "Radiation"
    assert result["created"] is True
    assert result["added"] == ["ZoneTestSuit", "ZoneTestBase", "ZoneTestGhost"]
    assert result["traders"] == [2]

    d = _tp_dir(rz)
    price = parse_price_config(d / "TraderPlusPriceConfig.json")
    cat = price.categories[-1]
    assert cat.name == "Radiation"
    assert [p.to_string() for p in cat.products] == [
        "ZoneTestSuit,1,-1,0,-1,-1", "ZoneTestBase,1,-1,0,-1,-1", "ZoneTestGhost,1,-1,0,-1,-1",
    ]
    ids = {e.id: e.categories for e in parse_ids_config(d / "TraderPlusIDsConfig.json").entries}
    assert ids == {1: ["Medic"], 2: ["Radiation"]}


def test_adding_into_an_existing_category_keeps_what_it_had(rz):
    zone_trader.add_to_traderplus(rz, "radzone", "Medic", [1])
    price = parse_price_config(_tp_dir(rz) / "TraderPlusPriceConfig.json")
    assert [c.name for c in price.categories] == ["Medic"]
    assert [p.classname for p in price.categories[0].products] == [
        "ZoneTestCure", "ZoneTestSuit", "ZoneTestBase", "ZoneTestGhost",
    ]
    assert price.categories[0].products[0].buy_price == 300
    ids = parse_ids_config(_tp_dir(rz) / "TraderPlusIDsConfig.json").entries
    assert ids[0].categories == ["Medic"]  # not listed twice


def test_a_second_add_has_nothing_left_and_writes_nothing(rz):
    zone_trader.add_to_traderplus(rz, "radzone", "Radiation", [])
    path = _tp_dir(rz) / "TraderPlusPriceConfig.json"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="already in the price file"):
        zone_trader.add_to_traderplus(rz, "radzone", "Radiation", [])
    assert path.read_text(encoding="utf-8") == before


def test_an_unknown_trader_fails_before_either_file_is_written(rz):
    path = _tp_dir(rz) / "TraderPlusPriceConfig.json"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="No trader with ID 9"):
        zone_trader.add_to_traderplus(rz, "radzone", "Radiation", [2, 9])
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(("key", "category", "match"), [
    ("nope", "X", "Unknown zone mod"),
    ("radzone", "  ", "Name the category"),
    ("toxiczone", "X", "ToxicZone is not installed"),
])
def test_bad_requests_are_refused(rz, key, category, match):
    with pytest.raises(ValueError, match=match):
        zone_trader.add_to_traderplus(rz, key, category, [])


def test_radzone_never_uses_a_trader_txt(rz):
    # D10: only ToxicZone's prices may be used. A Trader.txt in RadZone
    # changes nothing, and asking for its prices is refused.
    (rz.server_dir_path / RADZONE / "Trader.txt").write_text(
        "<Category> Test\n\tZoneTestSuit, *, 999, 111\n", encoding="utf-8"
    )
    assert _offer(rz)["prices"] == {}
    with pytest.raises(ValueError, match="no Trader.txt prices"):
        zone_trader.add_to_traderplus(rz, "radzone", "Radiation", [], use_prices=True)
    zone_trader.add_to_traderplus(rz, "radzone", "Radiation", [])
    price = parse_price_config(_tp_dir(rz) / "TraderPlusPriceConfig.json")
    suit = next(p for p in price.categories[-1].products if p.classname == "ZoneTestSuit")
    assert (suit.buy_price, suit.sell_price) == (-1, -1)


# Our own classes and numbers, laid out the way a DrJones-style Trader.txt is:
# a header, tab-separated rows, a repeated class, a trailing space, and a row
# with no price.
TRADER_TXT = (
    "\r\n\r\nSOME NOTE FOR THE ADMIN:\r\n\r\n<Category> TestZone\r\n"
    "\t\tZoneTestSuit,\t*,\t\t800,\t\t200\t\r\n"
    "\t\tZoneTestSuit,\t*,\t\t5,\t\t1\r\n"
    "\t\tZoneTestMask,   *,\t\t 70,           20\r\n"
    "\t\tZoneTestOnlyHere,\t*,\t\t10,\t\t5\r\n"
    "\t\tZoneTestBroken,\t*\r\n"
)


@pytest.fixture()
def tz(sample_config):
    """An installed ToxicZone with our own types file, a Trader.txt, and TraderPlus files."""
    cfg = sample_config
    write_test_mods(cfg, ['@CF', '@ToxicZone'])
    files = cfg.server_dir_path / "@ToxicZone" / "Xmls & Trader & Tools"
    files.mkdir(parents=True)
    (files / "types.xml").write_text(
        '<type name="ZoneTestSuit"/>\n<type name="ZoneTestMask"/>\n<type name="ZoneTestFilter"/>\n',
        encoding="utf-8",
    )
    (files / "Trader.txt").write_text(TRADER_TXT, encoding="utf-8")
    d = _tp_dir(cfg)
    (d / "TraderPlusPriceConfig.json").write_text(json.dumps({
        "Version": "2.5", "EnableAutoCalculation": 0, "EnableAutoDestockAtRestart": 0,
        "EnableDefaultTraderStock": 0, "TraderCategories": [],
    }), encoding="utf-8")
    (d / "TraderPlusIDsConfig.json").write_text(json.dumps({"Version": "2.5", "IDs": []}), encoding="utf-8")
    return cfg


def _tz_offer(cfg) -> dict:
    return next(o for o in zone_trader.offers(cfg)["offers"] if o["key"] == "toxiczone")


def test_toxiczone_offer_carries_the_installed_trader_txt_prices(tz):
    offer = _tz_offer(tz)
    assert offer["items"] == ["ZoneTestSuit", "ZoneTestMask", "ZoneTestFilter"]
    # First row wins; a class only in Trader.txt is not offered; an item the
    # file does not price has no entry.
    assert offer["prices"] == {
        "ZoneTestSuit": {"buy": 800.0, "sell": 200.0},
        "ZoneTestMask": {"buy": 70.0, "sell": 20.0},
    }
    assert offer["prices_source"].endswith("Trader.txt")


def _tz_products(cfg) -> dict:
    price = parse_price_config(_tp_dir(cfg) / "TraderPlusPriceConfig.json")
    return {p.classname: (p.buy_price, p.sell_price) for p in price.categories[-1].products}


def test_toxiczone_is_unpriced_unless_the_admin_asks(tz):
    result = zone_trader.add_to_traderplus(tz, "toxiczone", "Toxic", [])
    assert result["priced"] == 0
    assert set(_tz_products(tz).values()) == {(-1, -1)}


def test_toxiczone_prices_are_used_when_asked(tz):
    result = zone_trader.add_to_traderplus(tz, "toxiczone", "Toxic", [], use_prices=True)
    assert result["priced"] == 2
    assert _tz_products(tz) == {
        "ZoneTestSuit": (800, 200), "ZoneTestMask": (70, 20), "ZoneTestFilter": (-1, -1),
    }


def test_no_trader_txt_means_no_prices(tz):
    (tz.server_dir_path / "@ToxicZone" / "Xmls & Trader & Tools" / "Trader.txt").unlink()
    assert _tz_offer(tz)["prices"] == {}
    with pytest.raises(ValueError, match="no Trader.txt prices"):
        zone_trader.add_to_traderplus(tz, "toxiczone", "Toxic", [], use_prices=True)


# ── Expansion Market ─────────────────────────────────────────────────


def _market_item(name, low=10, high=20, smin=1, smax=100, variants=()):
    return {
        "ClassName": name, "MaxPriceThreshold": high, "MinPriceThreshold": low,
        "SellPricePercent": -1.0, "MaxStockThreshold": smax, "MinStockThreshold": smin,
        "QuantityPercent": -1, "SpawnAttachments": [], "Variants": list(variants),
    }


@pytest.fixture()
def mk(rz):
    """RadZone plus an installed Expansion Market with files of our own."""
    write_test_mods(rz, mods_lines(f"@CF\n{RADZONE}\n@DayZ-Expansion-Market\n"))
    exp = rz.server_dir_path / rz.profiles_path / "ExpansionMod"
    (exp / "Market").mkdir(parents=True)
    (exp / "Traders").mkdir()
    (exp / "Market" / "Apparel.json").write_text(json.dumps({
        "m_Version": 99, "DisplayName": "#STR_TEST_APPAREL", "Icon": "TestIcon", "Color": "00FF00FF",
        "IsExchange": 0, "InitStockPercent": 50.0,
        "Items": [
            _market_item("zonetesthat", smin=0, smax=10),
            _market_item("zonetestcoat", smin=0, smax=10, variants=["ZoneTestBase"]),
            _market_item("zonetestboot", smin=1, smax=100),
        ],
    }), encoding="utf-8")
    (exp / "Traders" / "Medic.json").write_text(json.dumps({
        "m_Version": 13, "DisplayName": "Field Medic", "Categories": ["Apparel"], "Items": {},
    }), encoding="utf-8")
    (exp / "Traders" / "Tailor.json").write_text(json.dumps({
        "m_Version": 13, "DisplayName": "#STR_TEST_TAILOR", "Categories": [], "Items": {},
    }), encoding="utf-8")
    return rz


def _market_dir(cfg):
    return cfg.server_dir_path / cfg.profiles_path / "ExpansionMod"


def _row(name, low=100, high=200, smin=1, smax=50):
    return {"classname": name, "min_price": low, "max_price": high, "min_stock": smin, "max_stock": smax}


def test_market_offers_list_variants_as_sold_and_the_usual_stock(mk):
    body = zone_trader.market_offers(mk)
    assert body["market_dir"] == "profiles/ExpansionMod/Market"
    assert body["stock"] == {"min": 0, "max": 10}  # two of the three items
    assert body["traders"] == [
        {"id": "Medic", "names": ["Field Medic"], "categories": 1},
        {"id": "Tailor", "names": [], "categories": 0},  # a string-table key says nothing
    ]
    offer = body["offers"][0]
    # ZoneTestCure is only in TraderPlus's price file, which the Market ignores.
    assert offer["items"] == ["ZoneTestCure", "ZoneTestSuit", "ZoneTestGhost"]
    assert offer["skipped"] == [{"classname": "ZoneTestBase", "reason": "already in the Market, in Apparel"}]


def test_market_offers_carry_toxiczones_prices_for_the_fill_button(tz):
    # The Market dialog's "Fill from Trader.txt" reads these; RadZone gets none.
    write_test_mods(tz, ['@CF', '@ToxicZone', '@DayZ-Expansion-Market'])
    market = tz.server_dir_path / tz.profiles_path / "ExpansionMod" / "Market"
    market.mkdir(parents=True)
    (market / "Apparel.json").write_text(json.dumps({"Items": [_market_item("zonetesthat")]}), encoding="utf-8")
    offer = zone_trader.market_offers(tz)["offers"][0]
    assert offer["key"] == "toxiczone"
    assert offer["prices"]["ZoneTestSuit"] == {"buy": 800.0, "sell": 200.0}
    assert offer["prices_source"].endswith("Trader.txt")


def test_market_offers_give_radzone_no_prices(mk):
    assert zone_trader.market_offers(mk)["offers"][0]["prices"] == {}


def test_no_market_without_the_mod(rz):
    assert zone_trader.market_offers(rz)["offers"] == []


def test_adding_a_market_category_writes_the_file_and_the_traders(mk):
    result = zone_trader.add_to_market(
        mk, "radzone", "Rad_Gear", ["Medic", "Tailor"], 0,
        [_row("ZoneTestSuit"), _row("ZoneTestGhost", 5, 5, 1, 1)],
    )
    assert result["created"] is True
    assert result["added"] == ["ZoneTestSuit", "ZoneTestGhost"]
    doc = json.loads((_market_dir(mk) / "Market" / "Rad_Gear.json").read_text(encoding="utf-8"))
    assert (doc["m_Version"], doc["Icon"], doc["Color"], doc["InitStockPercent"]) == (99, "TestIcon", "00FF00FF", 50.0)
    assert doc["DisplayName"] == "Rad Gear"
    assert doc["Items"] == [
        _market_item("zonetestsuit", 100, 200, 1, 50),
        _market_item("zonetestghost", 5, 5, 1, 1),
    ]
    for trader in ("Medic", "Tailor"):
        cats = json.loads((_market_dir(mk) / "Traders" / f"{trader}.json").read_text(encoding="utf-8"))["Categories"]
        assert cats[-1] == "Rad_Gear:0"


def test_buy_and_sell_is_written_as_the_bare_name_and_never_twice(mk):
    zone_trader.add_to_market(mk, "radzone", "Rad_Gear", ["Medic"], 1, [_row("ZoneTestSuit")])
    zone_trader.add_to_market(mk, "radzone", "Rad_Gear", ["Medic"], 1, [_row("ZoneTestCure")])
    cats = json.loads((_market_dir(mk) / "Traders" / "Medic.json").read_text(encoding="utf-8"))["Categories"]
    assert cats == ["Apparel", "Rad_Gear"]
    doc = json.loads((_market_dir(mk) / "Market" / "Rad_Gear.json").read_text(encoding="utf-8"))
    assert [i["ClassName"] for i in doc["Items"]] == ["zonetestsuit", "zonetestcure"]


@pytest.mark.parametrize(("category", "rows", "traders", "match"), [
    ("Rad Gear", [_row("ZoneTestSuit")], [], "letters, digits and _"),
    ("../x", [_row("ZoneTestSuit")], [], "letters, digits and _"),
    ("Rad", [_row("ZoneTestBase")], [], "not one of the items"),
    ("Rad", [_row("ZoneTestSuit", 0, 10)], [], "gives the item away"),
    ("Rad", [_row("ZoneTestSuit", 30, 10)], [], "lowest price is above"),
    ("Rad", [_row("ZoneTestSuit", smin=5, smax=1)], [], "stock range"),
    ("Rad", [{**_row("ZoneTestSuit"), "max_price": 2.5}], [], "whole number"),
    ("Rad", [_row("ZoneTestSuit"), _row("zonetestsuit")], [], "listed twice"),
    ("Rad", [], [], "at least one"),
    ("Rad", [_row("ZoneTestSuit")], ["Nobody"], "No trader file named Nobody"),
])
def test_a_bad_market_request_writes_nothing(mk, category, rows, traders, match):
    before = {p: p.read_text(encoding="utf-8") for p in _market_dir(mk).rglob("*.json")}
    with pytest.raises(ValueError, match=match):
        zone_trader.add_to_market(mk, "radzone", category, traders, 1, rows)
    after = {p: p.read_text(encoding="utf-8") for p in _market_dir(mk).rglob("*.json")}
    assert after == before
