"""Loot economy analysis — balance scoring, rarity classification, issue detection.

Analyzes types.xml entries against community-validated rules to produce
a scored report with per-item and per-category breakdowns.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.xml.types_xml import TypeEntry

# ---------------------------------------------------------------------------
# Rarity classification
# ---------------------------------------------------------------------------

RARITY_TIERS: dict[str, tuple[int, int]] = {
    "disabled": (0, 0),
    "legendary": (1, 2),
    "very_rare": (3, 5),
    "rare": (6, 20),
    "uncommon": (21, 50),
    "common": (51, 9999),
}

RARITY_LABELS: dict[str, str] = {
    "disabled": "Disabled (nominal 0 — won't spawn as loot)",
    "legendary": "Legendary (1–2 nominal — extremely rare finds)",
    "very_rare": "Very Rare (3–5 nominal — hard to come by)",
    "rare": "Rare (6–20 nominal — occasionally found)",
    "uncommon": "Uncommon (21–50 nominal — fairly regular spawns)",
    "common": "Common (51+ nominal — abundant everywhere)",
}

# Category keywords for classification from types.xml category field
_CATEGORY_MAP = {
    "weapons": "weapons",
    "food": "food",
    "clothes": "clothing",
    "containers": "containers",
    "tools": "tools",
    "vehiclesparts": "vehicles",
    "explosives": "explosives",
}

# Classname prefix/substring patterns for fallback category detection
_CLASSNAME_PATTERNS: list[tuple[list[str], str]] = [
    (["rifle", "pistol", "shotgun", "smg", "lmg", "launcher", "glock",
      "magnum", "mosin", "akm", "ak74", "ak101", "m4a1", "fal", "svd",
      "ump", "mp5", "saiga", "sks", "cz", "fnx", "deagle", "derringer",
      "ij70", "longhorn", "trumpet", "vaiga", "vss", "aug", "le_mas",
      "pioneer", "blaze", "repeater", "bk_", "cr527", "sg5",
      "ammo_", "mag_", "_mag", "suppressor", "optic_", "bayonet",
      "compensator", "handguard", "buttstock", "ghillie"], "weapons"),
    (["sardines", "tuna", "bacon", "peach", "tactical", "beans", "spaghetti",
      "fruit_", "vegetable_", "meat_", "apple", "pear", "plum", "pepper",
      "tomato", "potato", "zucchini", "pumpkin", "mushroom", "rice",
      "cereal", "powdered", "crackers", "canteen", "waterbottle", "soda",
      "drink_", "bottle_"], "food"),
    (["shirt", "pants", "jacket", "hoodie", "vest", "hat_", "helmet",
      "boots", "shoes", "gloves", "mask_", "balaclava", "bandana",
      "armband", "dress", "skirt", "coat", "raincoat", "gorka",
      "ttsko", "bdu", "rider", "cargo", "jeans", "tracksuit",
      "sneakers", "wellingtons", "militaryboots", "combatboots",
      "assaultboots", "jungleboots", "hikingboots"], "clothing"),
    (["backpack", "case", "pouch", "chest", "barrel", "crate",
      "drybag", "protector", "seachest", "improvisedbag",
      "sling_", "teddy", "briefcase", "giftbox"], "containers"),
    (["axe", "hatchet", "knife", "saw", "shovel", "pickaxe", "wrench",
      "screwdriver", "pliers", "hammer", "crowbar", "hacksaw",
      "pipe", "nail", "tape", "rope", "wire", "lock", "dial",
      "compass", "map", "gps", "radio", "megaphone", "battery",
      "spotlight", "flashlight", "headtorch", "binoculars",
      "rangefinder", "nvg", "epoxy", "stone", "whetstone",
      "sharpening", "sewing", "leather", "tanned"], "tools"),
    (["hatchback", "sedan", "offroad", "truck", "bus", "olga",
      "ada", "gunter", "sarka", "lada", "sparkplug", "carbattery",
      "headlightbulb", "wheel", "radiator", "door_", "hood_",
      "trunk_"], "vehicles"),
    (["grenade", "mine", "claymore", "explosive", "c4_", "tnt",
      "landmine", "tripwire", "bear_trap"], "explosives"),
    (["morphine", "epinephrine", "saline", "blood", "bandage",
      "splint", "tetracycline", "charcoal", "codeine", "vitamin",
      "painkiller", "disinfectant", "iodine", "firstaid",
      "injection", "syringe", "thermometer", "defibrillator",
      "iv_saline", "iv_blood"], "medical"),
]


def classify_rarity(nominal: int) -> str:
    """Classify an item's rarity tier based on its nominal value."""
    for tier, (lo, hi) in RARITY_TIERS.items():
        if lo <= nominal <= hi:
            return tier
    return "common"


def classify_category(entry: TypeEntry) -> str:
    """Classify item category from the types.xml category field."""
    cat = entry.category.lower() if entry.category else ""
    result = _CATEGORY_MAP.get(cat, "")
    if result:
        return result
    # Non-empty category not in the known map — return as-is
    if cat:
        return cat

    # Fallback: match classname against known patterns
    return category_from_classname(entry.name) or "other"


_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def class_words(name: str) -> set[str]:
    """The lowercase words of a class name, split at ``_``, digits and
    CamelCase: ``M134Minigun_Mag`` gives m, 134, minigun and mag."""
    return {w.lower() for w in _WORD_RE.findall(name)}


def category_from_classname(name: str, *, whole_words: bool = False) -> str:
    """The analyzer category that *name*'s keywords suggest (``weapons``,
    ``food``, ``clothing``, …), or "" when no keyword matches.

    *whole_words* matches a keyword only as a whole word of the name, so
    ``Showcase`` is not a ``case``. Auto configure uses that, since its guess
    decides where an item spawns, not only an icon."""
    if whole_words:
        words = class_words(name)
        for keywords, category in _CLASSNAME_PATTERNS:
            if any(kw.strip("_") in words for kw in keywords):
                return category
        return ""
    cn = name.lower()
    for keywords, category in _CLASSNAME_PATTERNS:
        if any(kw in cn for kw in keywords):
            return category
    return ""


_MEDICAL_KEYWORDS = next(kw for kw, cat in _CLASSNAME_PATTERNS if cat == "medical")

# Icon groups for what the analyzer calls "other": in the stock Chernarus
# types.xml that is 281 items with no category, mostly infected (Zmb*),
# animals (Animal_*) and map objects (Land_*, StaticObj_*, Wreck_*). The
# scoring never sees these groups.
_ICON_PREFIXES: list[tuple[tuple[str, ...], str]] = [
    (("zmb",), "infected"),
    (("animal_",), "animals"),
    (("boat_",), "boats"),
    (("land_", "staticobj_", "static_", "wreck_"), "world"),
]
# Finer icons inside a category, from a name keyword, first match wins:
# (analyzer category or icon group, keywords, icon kind). Checked after the
# groups above, so a "world" kind such as a train wreck works too.
_ICON_KINDS: list[tuple[str, tuple[str, ...], str]] = [
    ("tools", ("bone",), "bone"),
    ("tools", ("knife",), "knife"),
    ("tools", ("alarmclock",), "clock"),
    ("tools", ("firework",), "fireworks"),
    ("tools", ("injector",), "injector"),
    ("tools", ("spray",), "spray"),
    ("tools", ("bait",), "bait"),
    ("tools", ("baseballbat",), "bat"),
    ("tools", ("binocular",), "binoculars"),
    ("weapons", ("bayonet",), "bayonet"),
    ("weapons", ("mag_",), "magazine"),
    ("weapons", ("ammobox",), "ammobox"),
    ("weapons", ("ammo", "buck", "slug"), "ammo"),
    ("weapons", ("bttstck", "buttstock"), "buttstock"),
    ("weapons", ("crossbow",), "crossbow"),
    ("weapons", ("shotgun",), "shotgun"),
    ("weapons", ("optic",), "optic"),
    ("weapons", ("pistol", "colt", "cz75", "glock", "deagle", "fnx", "mkii", "magnum"), "pistol"),
    ("food", ("mushroom",), "mushroom"),
    ("food", ("apple",), "apple"),
    ("clothing", ("gloves",), "gloves"),
    ("clothing", ("glasses",), "glasses"),
    ("clothing", ("armband",), "armband"),
    ("clothing", ("cap_",), "cap"),
    ("clothing", ("hat",), "hat"),  # Hatchback and Hatchet sit in other categories
    ("clothing", ("boots", "shoes", "sneakers", "wellies"), "shoes"),
    ("clothing", ("pants", "jeans", "shorts"), "pants"),
    ("clothing", ("mask", "balaclava"), "mask"),
    ("containers", ("ammobox",), "ammobox"),
    ("containers", ("bear", "teddy"), "teddy"),
    ("world", ("train",), "train"),
    ("world", ("sed0",), "car"),  # sed01, sed02: "sed" alone hits Trailer_Closed
    ("other", ("bonfire",), "bonfire"),
    ("other", ("fence",), "fence"),
    ("other", ("bottle",), "bottle"),
    ("other", ("tower",), "watchtower"),
]
# TraderPlus's own items, whatever category a types.xml files them under: a
# mod's types file often puts them all in tools. No vanilla class carries
# these words, except Lockpick, which is a lockpick there too.
_ICON_TRADERPLUS: list[tuple[tuple[str, ...], str]] = [
    (("money",), "money"),
    (("wallet",), "wallet"),
    (("lockpick",), "lockpick"),
    (("receipt",), "receipt"),
    (("vehiclecargo",), "cargo"),
]
_ICON_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("pelt", "deadfox"), "animals"),
    (("trout", "caviar"), "food"),
]


def icon_category(entry: TypeEntry) -> str:
    """The category the web UI's item icon shows (webui ItemIcon.jsx).

    Stock types.xml files medicine under `tools` (Morphine, BandageDressing,
    SalineBag) and Splint under `clothes`, so the medical name rule wins here.
    An item the analyzer calls `other` gets a group from its name when one
    fits, `_ICON_TRADERPLUS` names TraderPlus's items in any category, and `_ICON_KINDS` picks a finer icon inside a category. Scoring keeps `classify_category`, so a category score stays as it was.
    """
    name = entry.name.lower()
    if any(kw in name for kw in _MEDICAL_KEYWORDS):
        return "medical"
    category = classify_category(entry)
    if category == "other":
        prefixed = next((g for p, g in _ICON_PREFIXES if name.startswith(p)), None)
        keyword = next((g for kws, g in _ICON_KEYWORDS if any(kw in name for kw in kws)), None)
        category = prefixed or keyword or category
    # A trailing _ lets a keyword such as "cap_" match PoliceCap as well as
    # FlatCap_Red, and still miss HighCapacityVest.
    ended = name + "_"
    for keywords, kind in _ICON_TRADERPLUS:
        if any(kw in name for kw in keywords):
            return kind
    for kind_of, keywords, kind in _ICON_KINDS:
        if kind_of == category and any(kw in ended for kw in keywords):
            return kind
    return category


# ---------------------------------------------------------------------------
# Scoring rules
# ---------------------------------------------------------------------------


@dataclass
class ItemIssue:
    """A single issue detected for a types.xml entry."""

    severity: str  # "error", "warning", "info"
    message: str


def score_nominal_min_ratio(nominal: int, min_val: int) -> tuple[float, ItemIssue | None]:
    """Score the nominal/min relationship. Returns (score, optional issue)."""
    if nominal == 0:
        return 1.0, None  # intentionally zero (crafted/deloot)
    ratio = min_val / nominal
    if ratio < 0.1:
        return 0.3, ItemIssue(
            "warning",
            f"min ({min_val}) very low vs nominal ({nominal}) — extreme scarcity swings",
        )
    if ratio > 1:
        return 0.5, ItemIssue(
            "info",
            f"min ({min_val}) is above nominal ({nominal}) — nominal is the target count, "
            "so min should not exceed it",
        )
    if ratio > 0.9:
        return 0.5, ItemIssue(
            "info",
            f"min ({min_val}) ≈ nominal ({nominal}) — items never fluctuate",
        )
    if 0.4 <= ratio <= 0.75:
        return 1.0, None
    return 0.7, None


def score_lifetime(lifetime: int, category: str) -> tuple[float, ItemIssue | None]:
    """Score lifetime appropriateness for the item category."""
    hours = lifetime / 3600
    if category in ("weapons", "tools", "containers", "vehicles"):
        if hours < 1:
            return 0.3, ItemIssue(
                "warning",
                f"Lifetime {hours:.1f}h too short for {category} — may despawn before found",
            )
        if hours > 336:  # 14 days
            return 0.6, ItemIssue(
                "info",
                f"Lifetime {hours:.0f}h very long — may cause entity bloat",
            )
    elif category in ("food",):
        if hours > 48:
            return 0.7, ItemIssue(
                "info", f"Lifetime {hours:.0f}h long for food item"
            )
    return 1.0, None


def score_restock(restock: int, nominal: int) -> tuple[float, ItemIssue | None]:
    """Score restock timer relative to nominal."""
    if nominal == 0:
        return 1.0, None
    if restock == 0 and nominal > 50:
        return 0.6, ItemIssue(
            "info",
            f"Restock=0 with high nominal ({nominal}) — loot refills very fast",
        )
    if restock > 7200 and nominal < 10:  # >2h for rare items
        return 0.5, ItemIssue(
            "warning",
            f"Restock {restock}s ({restock // 60}m) very slow for rare item (nominal {nominal})",
        )
    return 1.0, None


def score_flags(flags: dict[str, int], category: str) -> tuple[float, ItemIssue | None]:
    """Check flag configuration for common mistakes."""
    if not flags.get("count_in_map", 1):
        return 0.4, ItemIssue(
            "warning",
            "count_in_map=0 — items on the ground don't count toward nominal limit",
        )
    return 1.0, None


# ---------------------------------------------------------------------------
# Item scoring
# ---------------------------------------------------------------------------


RULE_NAMES: list[str] = ["nominal_min_ratio", "lifetime", "restock", "flags"]


@dataclass
class ItemScore:
    """Scored analysis of a single types.xml entry."""

    classname: str
    category: str
    rarity_tier: str
    nominal: int
    min_val: int
    lifetime: int
    restock: int
    score: float  # 0.0-1.0
    issues: list[ItemIssue] = field(default_factory=list)
    rule_scores: dict[str, float] = field(default_factory=dict)


def score_item(entry: TypeEntry) -> ItemScore:
    """Score a single types.xml entry across all rules."""
    category = classify_category(entry)
    rarity = classify_rarity(entry.nominal)
    scores: list[float] = []
    issues: list[ItemIssue] = []
    per_rule: dict[str, float] = {}

    for rule_name, (check_fn, args) in zip(RULE_NAMES, [
        (score_nominal_min_ratio, (entry.nominal, entry.min)),
        (score_lifetime, (entry.lifetime, category)),
        (score_restock, (entry.restock, entry.nominal)),
        (score_flags, (entry.flags, category)),
    ]):
        s, issue = check_fn(*args)
        scores.append(s)
        per_rule[rule_name] = s
        if issue:
            issues.append(issue)

    avg_score = sum(scores) / len(scores) if scores else 1.0

    return ItemScore(
        classname=entry.name,
        category=category,
        rarity_tier=rarity,
        nominal=entry.nominal,
        min_val=entry.min,
        lifetime=entry.lifetime,
        restock=entry.restock,
        score=round(avg_score, 2),
        issues=issues,
        rule_scores=per_rule,
    )


# ---------------------------------------------------------------------------
# Economy report
# ---------------------------------------------------------------------------


RULE_DESCRIPTIONS: dict[str, str] = {
    "nominal_min_ratio": (
        "Checks that the min value is a healthy proportion of nominal. "
        "Too low = extreme scarcity swings. Too high = items never fluctuate."
    ),
    "lifetime": (
        "Checks that item lifetime (how long before despawn) is appropriate "
        "for the category. Too short = despawns before found. Too long = entity bloat."
    ),
    "restock": (
        "Checks restock timer relative to rarity. Fast restock + high nominal = "
        "instant refills. Slow restock + low nominal = items never respawn."
    ),
    "flags": (
        "Checks for common flag mistakes like count_in_map=0 "
        "(items on ground don't count toward nominal limit)."
    ),
}


@dataclass
class EconomyReport:
    """Full economy analysis report."""

    overall_score: float
    items_analyzed: int
    items_with_issues: int = 0
    disabled_count: int = 0
    issues_by_severity: dict[str, int] = field(default_factory=dict)
    category_scores: dict[str, float] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    rule_scores: dict[str, float] = field(default_factory=dict)
    item_scores: list[ItemScore] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    # Items per rarity tier, every tier in RARITY_TIERS order (an empty one
    # counts 0), for the Analysis → Economy charts. All items, vanilla ones too.
    rarity_counts: dict[str, int] = field(default_factory=dict)
    # The live file against the stock types.xml (compare_to_vanilla), or None
    # when Zedstead has no stock copy yet. With it, the rule scores above cover
    # only the items vanilla does not have.
    vanilla: VanillaComparison | None = None


def analyze_economy(
    types: list[TypeEntry],
    max_players: int = 60,
    *,
    stock: list[TypeEntry] | None = None,
) -> EconomyReport:
    """Analyze a full types.xml economy and produce a scored report.

    With *stock* (the stock db/types.xml's entries), vanilla is the baseline
    (2026-11-03): an item vanilla has is measured by how far it moved from its
    stock values, never by the rules of thumb, which flagged vanilla's own
    values. The rules then score only the items vanilla does not have.
    """
    item_scores: list[ItemScore] = []
    category_totals: dict[str, list[float]] = {}
    severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    rule_totals: dict[str, list[float]] = {r: [] for r in RULE_NAMES}

    vanilla = compare_to_vanilla(types, stock) if stock is not None else None
    stock_names = {t.name.lower() for t in stock or []}
    rarity_counts = {tier: 0 for tier in RARITY_TIERS}
    for entry in types:
        rarity_counts[classify_rarity(entry.nominal)] += 1
    if stock_names:
        types = [t for t in types if t.name.lower() not in stock_names]

    for entry in types:
        scored = score_item(entry)
        item_scores.append(scored)

        cat = scored.category
        if cat not in category_totals:
            category_totals[cat] = []
        category_totals[cat].append(scored.score)

        for issue in scored.issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1

        for rule_name in RULE_NAMES:
            rule_totals[rule_name].append(scored.rule_scores.get(rule_name, 1.0))

    # Category averages
    category_scores = {
        cat: round(sum(scores) / len(scores), 2)
        for cat, scores in category_totals.items()
        if scores
    }
    category_counts = {
        cat: len(scores)
        for cat, scores in category_totals.items()
        if scores
    }

    # Per-rule averages
    rule_scores = {
        rule: round(sum(vals) / len(vals), 2)
        for rule, vals in rule_totals.items()
        if vals
    }

    # Overall
    all_scores = [s.score for s in item_scores]
    overall = round(sum(all_scores) / len(all_scores), 2) if all_scores else 1.0
    items_with_issues = sum(1 for s in item_scores if s.issues)
    disabled_count = sum(1 for s in item_scores if s.rarity_tier == "disabled")

    # Recommendations
    recommendations = _generate_recommendations(
        item_scores, category_scores, max_players
    )

    return EconomyReport(
        overall_score=overall,
        items_analyzed=len(item_scores),
        items_with_issues=items_with_issues,
        disabled_count=disabled_count,
        issues_by_severity=severity_counts,
        category_scores=category_scores,
        category_counts=category_counts,
        rule_scores=rule_scores,
        item_scores=item_scores,
        recommendations=recommendations,
        rarity_counts=rarity_counts,
        vanilla=vanilla,
    )


# ---------------------------------------------------------------------------
# Vanilla as the baseline
# ---------------------------------------------------------------------------

# The values compared with the stock file. A number is compared by how many
# times larger or smaller it got; the rest only by whether it changed.
VANILLA_NUMBERS: list[str] = ["nominal", "min", "lifetime", "restock", "quantmin", "quantmax", "cost"]
VANILLA_OTHER: list[str] = ["category", "flags", "usages", "values", "tag"]
# A changed value that is not a number counts as this much drift (a doubling).
_OTHER_DRIFT = 1.0
CHANGED_SHOWN = 200


@dataclass
class VanillaChange:
    """One value of one item that differs from the stock file."""

    field: str
    stock: object
    now: object


@dataclass
class VanillaItem:
    """A vanilla item whose live values differ from its stock ones."""

    classname: str
    category: str
    # How far it moved: the largest change, in doublings (log2 of the factor).
    drift: float
    turned_off: bool = False
    removed: bool = False
    changes: list[VanillaChange] = field(default_factory=list)


@dataclass
class VanillaComparison:
    """The live types against the stock db/types.xml."""

    vanilla_items: int
    unchanged: int
    # unchanged / vanilla_items: 1.0 for a stock file.
    closeness: float
    turned_off: int = 0
    removed: int = 0
    # Live items vanilla does not have (mods, custom), which the rules score.
    other_items: int = 0
    # {category: [changed, vanilla items]}
    changed_by_category: dict[str, list[int]] = field(default_factory=dict)
    # The most changed first, at most CHANGED_SHOWN.
    changed: list[VanillaItem] = field(default_factory=list)
    changed_count: int = 0


def _drift(stock: object, now: object) -> float:
    """How far a number moved, in doublings; a 0 counts as 1 so turning an
    item off reads as a large move, not an infinite one."""
    import math

    a, b = max(float(stock or 0), 0.0) + 1, max(float(now or 0), 0.0) + 1
    return abs(math.log2(b / a))


def compare_to_vanilla(types: list[TypeEntry], stock: list[TypeEntry]) -> VanillaComparison:
    """Compare each stock item with its live entry.

    A name in several live files counts as its last one, the file the
    economy reads last (custom/ after db/). A stock item missing from every
    live file is ``removed``.
    """
    live = {t.name.lower(): t for t in types}
    stock_by_name = {t.name.lower(): t for t in stock}
    changed: list[VanillaItem] = []
    by_category: dict[str, list[int]] = {}
    for key, base in stock_by_name.items():
        category = classify_category(base)
        counts = by_category.setdefault(category, [0, 0])
        counts[1] += 1
        now = live.get(key)
        if now is None:
            item = VanillaItem(base.name, category, drift=_drift(base.nominal, 0), removed=True)
        else:
            diffs = [
                VanillaChange(f, getattr(base, f), getattr(now, f))
                for f in VANILLA_NUMBERS + VANILLA_OTHER
                if getattr(base, f) != getattr(now, f)
            ]
            if not diffs:
                continue
            drift = max(
                _drift(d.stock, d.now) if d.field in VANILLA_NUMBERS else _OTHER_DRIFT for d in diffs
            )
            item = VanillaItem(
                base.name, category, drift=round(drift, 2),
                turned_off=base.nominal > 0 and now.nominal == 0, changes=diffs,
            )
        counts[0] += 1
        changed.append(item)
    changed.sort(key=lambda i: (-i.drift, i.classname.lower()))
    total = len(stock_by_name)
    unchanged = total - len(changed)
    return VanillaComparison(
        vanilla_items=total,
        unchanged=unchanged,
        closeness=round(unchanged / total, 2) if total else 1.0,
        turned_off=sum(1 for i in changed if i.turned_off),
        removed=sum(1 for i in changed if i.removed),
        other_items=sum(1 for k in live if k not in stock_by_name),
        changed_by_category=by_category,
        changed=changed[:CHANGED_SHOWN],
        changed_count=len(changed),
    )


def _generate_recommendations(
    items: list[ItemScore],
    cat_scores: dict[str, float],
    max_players: int,
) -> list[str]:
    """Generate actionable recommendations from the analysis."""
    recs: list[str] = []

    # Worst categories
    for cat, score in sorted(cat_scores.items(), key=lambda x: x[1]):
        if score < 0.6:
            count = sum(1 for i in items if i.category == cat and i.issues)
            recs.append(f"{cat.capitalize()}: score {score:.0%} — {count} items with issues")

    # Items with most issues
    worst = sorted(items, key=lambda i: i.score)[:5]
    for item in worst:
        if item.score < 0.5 and item.issues:
            recs.append(f"{item.classname}: {item.issues[0].message}")

    return recs[:10]  # cap at 10
