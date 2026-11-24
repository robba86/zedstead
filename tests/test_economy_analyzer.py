"""Tests for cli.ops.economy_analyzer — loot balance scoring and analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.ops.economy_analyzer import (
    EconomyReport,
    ItemScore,
    analyze_economy,
    classify_category,
    classify_rarity,
    score_flags,
    score_item,
    score_lifetime,
    score_nominal_min_ratio,
    score_restock,
)


# ---------------------------------------------------------------------------
# Minimal TypeEntry mock (avoids importing the full types_xml module)
# ---------------------------------------------------------------------------


@dataclass
class _MockType:
    name: str
    nominal: int
    min: int
    lifetime: int
    restock: int
    quantmin: int = -1
    quantmax: int = -1
    cost: int = 100
    category: str = "weapons"
    tag: str = ""
    flags: dict[str, int] = field(default_factory=lambda: {"count_in_map": 1})
    usages: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    source_file: Path = field(default_factory=lambda: Path())
    element: ET.Element = field(default_factory=lambda: ET.Element("type"))


def _t(name: str = "AKM", nominal: int = 10, min_val: int = 5,
       lifetime: int = 28800, restock: int = 1800,
       category: str = "weapons", **kw) -> _MockType:
    return _MockType(name=name, nominal=nominal, min=min_val,
                     lifetime=lifetime, restock=restock, category=category, **kw)


# ---------------------------------------------------------------------------
# Rarity classification
# ---------------------------------------------------------------------------


class TestClassifyRarity:
    def test_disabled(self):
        assert classify_rarity(0) == "disabled"

    def test_legendary(self):
        assert classify_rarity(1) == "legendary"
        assert classify_rarity(2) == "legendary"

    def test_very_rare(self):
        assert classify_rarity(3) == "very_rare"

    def test_rare(self):
        assert classify_rarity(10) == "rare"

    def test_uncommon(self):
        assert classify_rarity(30) == "uncommon"

    def test_common(self):
        assert classify_rarity(100) == "common"


class TestClassifyCategory:
    def test_known_categories(self):
        assert classify_category(_t(category="weapons")) == "weapons"
        assert classify_category(_t(category="food")) == "food"
        assert classify_category(_t(category="clothes")) == "clothing"

    def test_unknown_category(self):
        assert classify_category(_t(name="SomeCustomThing", category="custom")) == "custom"

    def test_empty_category_with_classname_fallback(self):
        # AKM matches weapons via classname pattern
        assert classify_category(_t(name="AKM", category="")) == "weapons"

    def test_empty_category_no_match(self):
        # Unknown classname falls to "other"
        assert classify_category(_t(name="SomeUnknownItem", category="")) == "other"


# ---------------------------------------------------------------------------
# Individual scoring rules
# ---------------------------------------------------------------------------


class TestScoreNominalMinRatio:
    def test_ideal_ratio(self):
        score, issue = score_nominal_min_ratio(100, 50)
        assert score == 1.0
        assert issue is None

    def test_very_low_min(self):
        score, issue = score_nominal_min_ratio(100, 5)
        assert score < 0.5
        assert issue is not None
        assert issue.severity == "warning"

    def test_min_equals_nominal(self):
        score, issue = score_nominal_min_ratio(100, 95)
        assert score < 1.0
        assert issue is not None

    def test_min_above_nominal_says_so(self):
        score, issue = score_nominal_min_ratio(5, 10)
        assert (score, issue.severity) == (0.5, "info")
        assert issue.message.startswith("min (10) is above nominal (5)")
        assert "≈" not in issue.message

        _, equal = score_nominal_min_ratio(5, 5)
        assert equal.message == "min (5) ≈ nominal (5) — items never fluctuate"

    def test_zero_nominal(self):
        score, issue = score_nominal_min_ratio(0, 0)
        assert score == 1.0


class TestScoreLifetime:
    def test_normal_weapon(self):
        score, issue = score_lifetime(28800, "weapons")  # 8 hours
        assert score == 1.0

    def test_very_short_weapon(self):
        score, issue = score_lifetime(1800, "weapons")  # 30 min
        assert score < 0.5
        assert issue is not None

    def test_very_long_weapon(self):
        score, issue = score_lifetime(14 * 24 * 3600 + 1, "weapons")  # >14 days
        assert score < 1.0

    def test_long_food(self):
        score, issue = score_lifetime(3 * 24 * 3600, "food")  # 3 days
        assert score < 1.0


class TestScoreRestock:
    def test_normal(self):
        score, issue = score_restock(1800, 10)
        assert score == 1.0

    def test_instant_restock_high_nominal(self):
        score, issue = score_restock(0, 100)
        assert score < 1.0

    def test_very_slow_rare(self):
        score, issue = score_restock(7201, 5)
        assert score < 0.7
        assert issue is not None


class TestScoreFlags:
    def test_normal_flags(self):
        score, issue = score_flags({"count_in_map": 1}, "weapons")
        assert score == 1.0

    def test_no_count_in_map(self):
        score, issue = score_flags({"count_in_map": 0}, "weapons")
        assert score < 0.5
        assert issue is not None


# ---------------------------------------------------------------------------
# Item scoring
# ---------------------------------------------------------------------------


class TestScoreItem:
    def test_balanced_item(self):
        entry = _t(nominal=30, min_val=15, lifetime=28800, restock=1800)
        scored = score_item(entry)
        assert scored.score > 0.8
        assert scored.rarity_tier == "uncommon"
        assert scored.category == "weapons"

    def test_broken_item(self):
        entry = _t(nominal=100, min_val=1, lifetime=600, restock=0,
                    flags={"count_in_map": 0})
        scored = score_item(entry)
        assert scored.score < 0.5
        assert len(scored.issues) >= 2

    def test_crafted_item(self):
        entry = _t(nominal=0, min_val=0, lifetime=3600, restock=0)
        scored = score_item(entry)
        assert scored.score >= 0.7  # crafted items are fine with 0 nominal


# ---------------------------------------------------------------------------
# Economy report
# ---------------------------------------------------------------------------


class TestAnalyzeEconomy:
    def test_basic_report(self):
        types = [
            _t("AKM", 10, 5, 28800, 1800, "weapons"),
            _t("Apple", 100, 50, 14400, 600, "food"),
            _t("Bandage", 50, 25, 14400, 600, "tools"),
        ]
        report = analyze_economy(types)
        assert isinstance(report, EconomyReport)
        assert report.items_analyzed == 3
        assert report.overall_score > 0
        assert "weapons" in report.category_scores
        assert "food" in report.category_scores

    def test_empty_types(self):
        report = analyze_economy([])
        assert report.items_analyzed == 0
        assert report.overall_score == 1.0

    def test_recommendations_for_bad_economy(self):
        types = [
            _t("Broken1", 100, 1, 600, 0, "weapons", flags={"count_in_map": 0}),
            _t("Broken2", 100, 2, 600, 0, "weapons", flags={"count_in_map": 0}),
        ]
        report = analyze_economy(types)
        assert len(report.recommendations) > 0
        assert report.issues_by_severity.get("warning", 0) > 0


# ---------------------------------------------------------------------------
# Vanilla as the baseline (compare_to_vanilla, analyze_economy(stock=))
# ---------------------------------------------------------------------------


class TestVanillaBaseline:
    """Vanilla is "good": a stock item is measured against its stock values,
    and the rules of thumb score only the items vanilla does not have."""

    def test_a_stock_file_is_fully_like_vanilla(self):
        # Values the rules flag (min far below nominal, restock 0) still
        # score 100% when they are vanilla's own.
        stock = [_t("AKM", nominal=60, min_val=2, restock=0), _t("Apple", category="food")]
        report = analyze_economy([_t("AKM", nominal=60, min_val=2, restock=0), _t("Apple", category="food")], stock=stock)
        assert report.vanilla.closeness == 1.0
        assert report.vanilla.changed == []
        assert report.items_analyzed == 0

    def test_changes_are_listed_biggest_first(self):
        stock = [_t("M4A1", nominal=3), _t("NVG", nominal=2), _t("Apple", lifetime=100, category="food")]
        live = [_t("M4A1", nominal=30), _t("NVG", nominal=0), _t("Apple", lifetime=110, category="food")]
        v = analyze_economy(live, stock=stock).vanilla
        assert v.unchanged == 0 and v.closeness == 0.0
        assert [i.classname for i in v.changed] == ["M4A1", "NVG", "Apple"]
        assert v.changed[1].turned_off and v.turned_off == 1
        assert v.changed[0].changes[0].field == "nominal"
        assert (v.changed[0].changes[0].stock, v.changed[0].changes[0].now) == (3, 30)

    def test_a_removed_stock_item_and_mod_items(self):
        stock = [_t("AKM"), _t("Apple", category="food")]
        live = [_t("AKM"), _t("ModGun_1"), _t("ModGun_2")]
        report = analyze_economy(live, stock=stock)
        v = report.vanilla
        assert v.removed == 1 and v.changed[0].classname == "Apple"
        assert v.other_items == 2
        # The rules see only the two mod items; rarity counts every live item.
        assert report.items_analyzed == 2
        assert sum(report.rarity_counts.values()) == 3

    def test_without_stock_every_item_is_ruled(self):
        report = analyze_economy([_t("AKM"), _t("Apple", category="food")])
        assert report.vanilla is None
        assert report.items_analyzed == 2
