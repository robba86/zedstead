"""Tests for a TraderPlus product line zsm cannot read, through /api/tp-price*
(app/web/routers/configs.py)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"

GOOD_LINE = "M4A1,1,10,1,1000,500"
BAD_LINES = ["AKM,1,-1,1,100", "AKM,1,-1,1,,50", "AKM,1,inf,1,100,50", 5, None]
PRICE_CONFIG = {
    "Version": "2.5",
    "EnableAutoCalculation": 0,
    "EnableAutoDestockAtRestart": 0,
    "EnableDefaultTraderStock": 0,
    "TraderCategories": [
        {"CategoryName": "Food", "Products": ["Apple,1,100,1,10,5"]},
        {"CategoryName": "Weapons", "Products": [GOOD_LINE, *BAD_LINES]},
    ],
}


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def price_file(sample_config):
    d = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "TraderPlusPriceConfig.json"
    path.write_text(json.dumps(PRICE_CONFIG, indent=4), encoding="utf-8")
    return path


def _weapons(path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["TraderCategories"][1]["Products"]


def test_the_list_marks_and_counts_the_lines_it_cannot_read(client, price_file):
    resp = client.get("/api/tp-price")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 7
    assert body["unreadable"] == len(BAD_LINES)
    by_id = {row["id"]: row for row in body["rows"]}
    assert "unreadable" not in by_id["1:0"]
    assert [by_id[f"1:{i}"].get("unreadable") for i in range(1, 6)] == [True] * 5
    assert [by_id[f"1:{i}"]["classname"] for i in range(1, 6)] == [
        "AKM,1,-1,1,100", "AKM,1,-1,1,,50", "AKM,1,inf,1,100,50", "5", "null",
    ]


def test_the_count_covers_the_whole_file_whatever_the_search(client, price_file):
    body = client.get("/api/tp-price", params={"q": "apple"}).json()

    assert [row["classname"] for row in body["rows"]] == ["Apple"]
    assert body["unreadable"] == len(BAD_LINES)


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": -5}, {"limit": 0}])
def test_a_negative_offset_or_limit_is_refused(client, price_file, params):
    """A negative slice read from the end of the list instead of a page."""
    assert client.get("/api/tp-price", params=params).status_code == 422


def test_editing_a_good_row_leaves_the_unreadable_lines_as_they_were(client, price_file):
    resp = client.put("/api/tp-price/1/0", json={"buy_price": 2000})

    assert resp.status_code == 200, resp.text
    assert resp.json()["buy_price"] == 2000
    assert _weapons(price_file) == ["M4A1,1,10,1,2000,500", *BAD_LINES]
    text = price_file.read_text(encoding="utf-8")
    assert all(f'"{line}"' in text for line in BAD_LINES[:3])


def test_editing_an_unreadable_row_is_refused_and_names_the_line(client, price_file, sample_config):
    from app.shared.config_versions import load_index, store_for

    before = price_file.read_bytes()

    resp = client.put("/api/tp-price/1/1", json={"buy_price": 2000})

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "TraderPlusPriceConfig.json" in detail
    # The price file has no Raw tab, so the sentence must not send anyone to one.
    assert "Weapons" in detail and "line 2" in detail and "in the file itself" in detail
    assert "Raw tab" not in detail
    assert price_file.read_bytes() == before
    assert load_index(store_for(price_file)).entries == []


@pytest.mark.parametrize("prod_idx", [2, 3, 4, 5])
def test_every_unreadable_shape_is_refused_in_a_sentence(client, price_file, prod_idx):
    resp = client.put(f"/api/tp-price/1/{prod_idx}", json={"buy_price": 2000})

    assert resp.status_code == 400, resp.text
    assert f"line {prod_idx + 1}" in resp.json()["detail"]
    assert _weapons(price_file) == [GOOD_LINE, *BAD_LINES]


def test_the_multiplier_leaves_the_unreadable_lines_alone(client, price_file):
    resp = client.post(
        "/api/tp-price/1/multiplier",
        json={"multiplier": 2.0, "columns": ["buy_price", "sell_price", "coefficient", "max_stock"]},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["changes"] == 4
    assert _weapons(price_file) == ["M4A1,2,20,1,2000,1000", *BAD_LINES]


@pytest.mark.parametrize("multiplier", [0, -2, 10.5, 1000])
def test_a_multiplier_out_of_range_is_refused_before_anything_is_kept(client, price_file, multiplier):
    """An emptied box sent 0, which apply_price_multiplier clamps to 1, and
    every buy and sell price in the category became 1."""
    from app.shared.config_versions import load_index, store_for

    before = price_file.read_bytes()
    resp = client.post(
        "/api/tp-price/1/multiplier",
        json={"multiplier": multiplier, "columns": ["buy_price", "sell_price"]},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "The price multiplier must be above 0 and at most 10."
    assert price_file.read_bytes() == before
    assert not load_index(store_for(price_file)).get_versions(price_file.name)


def test_an_unknown_category_keeps_no_version(client, price_file):
    from app.shared.config_versions import load_index, store_for

    resp = client.post("/api/tp-price/99/multiplier", json={"multiplier": 2.0})
    assert resp.status_code == 400
    assert not load_index(store_for(price_file)).get_versions(price_file.name)


def test_the_category_filter_lists_one_category(client, price_file):
    body = client.get("/api/tp-price", params={"category": "Food"}).json()

    assert [row["classname"] for row in body["rows"]] == ["Apple"]
    assert body["total"] == 1
    assert body["categories"] == ["Food", "Weapons"]


def test_the_trade_filter_reads_minus_one_as_off_and_skips_unreadable_lines(client, price_file):
    config = json.loads(price_file.read_text(encoding="utf-8"))
    config["TraderCategories"][0]["Products"] += ["Pear,1,100,1,-1,5", "Plum,1,100,1,10,-1", "Fig,1,100,1,-1,-1"]
    price_file.write_text(json.dumps(config), encoding="utf-8")

    def names(trade):
        return [row["classname"] for row in client.get("/api/tp-price", params={"trade": trade}).json()["rows"]]

    assert names("buy") == ["Apple", "Plum", "M4A1"]
    assert names("sell") == ["Apple", "Pear", "M4A1"]
    assert names("buy_only") == ["Plum"]
    assert names("sell_only") == ["Pear"]
    assert names("off") == ["Fig"]
    assert client.get("/api/tp-price", params={"trade": "nope"}).status_code == 400
