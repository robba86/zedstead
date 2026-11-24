"""TraderPlus PriceConfig JSON parsing, editing, and backup/restore."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.shared.json_utils import load_json
from app.traderplus.trader_common import get_traderplus_config_dir, json_list, over_raw, refuse_in
from app.xml.xml_utils import backup_file, restore_file


# ---------------------------------------------------------------------------
# Field descriptions (shown as help in the web editor)
# ---------------------------------------------------------------------------

FIELD_DESCRIPTIONS: dict[str, str] = {
    "classname": "Item classname as defined in the game files.",
    "coefficient": (
        "Per-product price multiplier used by auto-calc. Above 1.0 = pricier, below = cheaper. "
        "Only takes effect when 'Enable auto calculation' is on (Global Settings); otherwise the "
        "literal buy_price/sell_price values are used."
    ),
    "max_stock": "Maximum trader stock. -1 = unlimited, -2 = auto-calculated from CE.",
    "trade_quantity": "Number of items per trade transaction.",
    "buy_price": "Price to buy from trader. -1 = item cannot be bought.",
    "sell_price": "Price to sell to trader. -1 = item cannot be sold.",
    "destock_coefficient": (
        "Per-product destock multiplier. Overrides the category-level destock at server restart "
        "when `enable_auto_destock` is on. Optional — leave blank to use the global default."
    ),
    "enable_auto_calculation": "Auto-calculate buy/sell prices based on item rarity.",
    "enable_auto_destock": (
        "Reset each trader's stock back to its baseline (per-product max_stock/destock_coefficient) "
        "on every server restart. Off = stock persists across restarts."
    ),
    "enable_default_stock": (
        "Initialize traders with the max_stock values from this config on first run/wipe. "
        "Off = traders start empty and rely on player sales for inventory."
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TraderPlusProduct:
    """One product entry (parsed from comma-separated string)."""

    classname: str = ""
    coefficient: float = 1.0
    max_stock: int = -1
    trade_quantity: float = 1.0
    buy_price: float = -1.0
    sell_price: float = -1.0
    destock_coefficient: float | None = None
    # A line that is not a product zsm can read (too few fields, a field that
    # is not a number, an entry that is not text) is kept as the file has it
    # and written back unchanged. No edit applies to it.
    raw: Any = None
    unreadable: bool = False
    #: The line as the file wrote it. Written back as it is while the product
    #: says the same, so an edit to one line leaves every other line's
    #: spelling (``0.50``, a space after a comma) alone.
    source: str | None = field(default=None, repr=False, compare=False)

    @staticmethod
    def from_string(s: Any) -> TraderPlusProduct:
        """Parse a product string like 'M4A1,0.4,-2,1,10000,0.8'."""
        try:
            return TraderPlusProduct._parse(s)
        except (ValueError, OverflowError):
            shown = s.strip() if isinstance(s, str) else json.dumps(s)
            return TraderPlusProduct(classname=shown, raw=s, unreadable=True)

    @staticmethod
    def _parse(s: Any) -> TraderPlusProduct:
        if not isinstance(s, str):
            raise ValueError("not text")
        parts = [p.strip() for p in s.split(",")]
        if len(parts) < 6:
            raise ValueError("fewer than six fields")

        p = TraderPlusProduct(
            classname=parts[0],
            coefficient=_finite(parts[1]),
            max_stock=int(_finite(parts[2])),
            trade_quantity=_finite(parts[3]),
            buy_price=_finite(parts[4]),
            sell_price=_finite(parts[5]),
        )
        if len(parts) >= 7 and parts[6]:
            p.destock_coefficient = _finite(parts[6])
        p.source = s
        return p

    def _fields(self) -> tuple:
        return (
            self.classname, self.coefficient, self.max_stock, self.trade_quantity,
            self.buy_price, self.sell_price, self.destock_coefficient,
        )

    def to_string(self) -> str:
        """Serialize back to comma-separated format."""
        parts = [
            self.classname,
            _fmt_num(self.coefficient),
            str(self.max_stock),
            _fmt_num(self.trade_quantity),
            _fmt_num(self.buy_price),
            _fmt_num(self.sell_price),
        ]
        if self.destock_coefficient is not None:
            parts.append(_fmt_num(self.destock_coefficient))
        return ",".join(parts)

    def to_entry(self) -> Any:
        """What the file holds for this product."""
        if self.unreadable:
            return self.raw
        if self.source is not None and TraderPlusProduct._parse(self.source)._fields() == self._fields():
            return self.source
        return self.to_string()

    def listed_class(self) -> str:
        """The class this line is about, as far as it can be told."""
        if self.unreadable:
            return self.raw.split(",")[0].strip() if isinstance(self.raw, str) else ""
        return self.classname


@dataclass
class TraderPlusCategory:
    """One trader category with its products."""

    name: str = ""
    products: list[TraderPlusProduct] = field(default_factory=list)
    #: The category as the file had it (``over_raw``).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass
class TraderPlusPriceConfig:
    """Parsed TraderPlusPriceConfig.json file."""

    path: Path = field(default_factory=lambda: Path())
    version: str = "2.5"
    enable_auto_calculation: int = 0
    enable_auto_destock: int = 0
    enable_default_stock: int = 0
    categories: list[TraderPlusCategory] = field(default_factory=list)
    #: The file as it was read: a save writes the known keys over it.
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    #: The edited keys as parsed (``over_raw``): a key the file lacks is saved only once it changes.
    _read: dict[str, Any] | None = field(default=None, repr=False, compare=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finite(text: str) -> float:
    """float(), refusing inf and nan: neither is a price, and int() of one raises."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {text!r}")
    return value


def _fmt_num(v: float) -> str:
    """Format a number: whole values as int, fractions in plain decimal.

    Uses fixed-point (not %g) so large or very small fractional values never
    serialize as scientific notation — e.g. 2500000.5 -> "2500000.5", not
    "2.5e+06", which TraderPlus's JSON parser cannot read. %g also silently
    dropped precision past 6 significant figures (1000000.5 -> "1e+06").
    """
    if v == int(v):
        return str(int(v))
    # Six decimal places covers trader coefficients/prices; trim trailing zeros.
    return f"{v:.6f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_price_config_path(config: DayzConfig) -> Path | None:
    """Return TraderPlusPriceConfig.json path if it exists."""
    cfg_dir = get_traderplus_config_dir(config)
    if not cfg_dir:
        return None
    p = cfg_dir / "TraderPlusPriceConfig.json"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Parse & save
# ---------------------------------------------------------------------------

def parse_price_config(path: Path) -> TraderPlusPriceConfig:
    """Parse TraderPlusPriceConfig.json."""
    data = load_json(path)

    categories: list[TraderPlusCategory] = []
    try:
        cat_list = json_list(data, "TraderCategories")
    except ValueError as exc:
        raise refuse_in(path, exc) from exc
    for i, cat_data in enumerate(cat_list):
        try:
            entries = json_list(cat_data, "Products")
        except ValueError as exc:
            raise refuse_in(path, exc, f"category {i + 1}") from exc
        products = [TraderPlusProduct.from_string(s) for s in entries]
        categories.append(TraderPlusCategory(
            name=cat_data.get("CategoryName", ""),
            products=products,
            raw=cat_data,
        ))

    cfg = TraderPlusPriceConfig(
        path=path,
        _raw=data,
        version=str(data.get("Version", "2.5")),
        enable_auto_calculation=int(data.get("EnableAutoCalculation", 0)),
        enable_auto_destock=int(data.get("EnableAutoDestockAtRestart", 0)),
        enable_default_stock=int(data.get("EnableDefaultTraderStock", 0)),
        categories=categories,
    )
    cfg._read = _known(cfg)
    return cfg


def _category(cat: TraderPlusCategory) -> dict[str, Any]:
    # A key a category from the file lacks read as the parser's default.
    read = {"CategoryName": "", "Products": []} if cat.raw else None
    return over_raw(cat.raw, {
        "CategoryName": cat.name,
        "Products": [p.to_entry() for p in cat.products],
    }, read)


def _known(cfg: TraderPlusPriceConfig) -> dict[str, Any]:
    """The keys the web editor edits, as *cfg* holds them."""
    return {
        "Version": cfg.version,
        "EnableAutoCalculation": cfg.enable_auto_calculation,
        "EnableAutoDestockAtRestart": cfg.enable_auto_destock,
        "EnableDefaultTraderStock": cfg.enable_default_stock,
        "TraderCategories": [_category(cat) for cat in cfg.categories],
    }


def save_price_config(cfg: TraderPlusPriceConfig) -> None:
    """Write TraderPlusPriceConfig.json over the file's own text: keys and
    lines nothing changed stay as they were."""
    data = over_raw(cfg._raw, _known(cfg), cfg._read)
    save_json_patched(cfg.path, data)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_price_config(path: Path) -> Path:
    """Create a .backup copy of the price config."""
    return backup_file(path)


def restore_price_config(path: Path) -> bool:
    """Restore price config from .backup. Returns True on success."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# CRUD — categories
# ---------------------------------------------------------------------------

def add_category(cfg: TraderPlusPriceConfig, name: str) -> TraderPlusCategory:
    """Add a new empty category."""
    cat = TraderPlusCategory(name=name)
    cfg.categories.append(cat)
    return cat


def remove_category(cfg: TraderPlusPriceConfig, index: int) -> None:
    """Remove a category by index."""
    if 0 <= index < len(cfg.categories):
        cfg.categories.pop(index)


def rename_category(cfg: TraderPlusPriceConfig, index: int, new_name: str) -> None:
    """Rename a category by index."""
    if 0 <= index < len(cfg.categories):
        cfg.categories[index].name = new_name


# ---------------------------------------------------------------------------
# CRUD — products
# ---------------------------------------------------------------------------

def add_product(cat: TraderPlusCategory, product: TraderPlusProduct) -> None:
    """Add a product to a category."""
    cat.products.append(product)


def remove_product(cat: TraderPlusCategory, index: int) -> None:
    """Remove a product by index."""
    if 0 <= index < len(cat.products):
        cat.products.pop(index)


def update_product(cat: TraderPlusCategory, index: int, product: TraderPlusProduct) -> None:
    """Replace a product at index."""
    if 0 <= index < len(cat.products):
        cat.products[index] = product


def move_product(cat: TraderPlusCategory, index: int, direction: int) -> int:
    """Swap product at ``index`` with its neighbour. ``direction``: -1 up, +1 down.

    Returns the new index (clamped to bounds; unchanged if at edge).
    """
    target = index + direction
    if 0 <= index < len(cat.products) and 0 <= target < len(cat.products):
        cat.products[index], cat.products[target] = cat.products[target], cat.products[index]
        return target
    return index


def move_category(cfg: TraderPlusPriceConfig, index: int, direction: int) -> int:
    """Swap category at ``index`` with its neighbour. ``direction``: -1 up, +1 down.

    Returns the new index (clamped to bounds; unchanged if at edge).
    """
    target = index + direction
    if 0 <= index < len(cfg.categories) and 0 <= target < len(cfg.categories):
        cfg.categories[index], cfg.categories[target] = cfg.categories[target], cfg.categories[index]
        return target
    return index


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

_NUMERIC_PRODUCT_FIELDS: tuple[str, ...] = (
    "coefficient",
    "max_stock",
    "trade_quantity",
    "buy_price",
    "sell_price",
    "destock_coefficient",
)


def apply_category_multiplier(
    cfg: TraderPlusPriceConfig,
    category_index: int | None,
    multiplier: float,
    columns: set[str] | None = None,
) -> list[tuple[str, str, str, float, float]]:
    """Apply a multiplier to selected numeric columns of a category (or all).

    ``columns`` defaults to ``{"buy_price", "sell_price"}`` — the original
    behaviour. Pass any subset of ``_NUMERIC_PRODUCT_FIELDS`` to scale those
    columns instead. Buy/sell preserve the original "stay >= 1, sell <= buy,
    skip -1 sentinels" clamping; other columns get plain multiplication.

    Returns ``(classname, category, column, old_value, new_value)`` for each
    individual field change.
    """
    from app.traderplus.trader_common import apply_price_multiplier

    cols = columns if columns is not None else {"buy_price", "sell_price"}
    changes: list[tuple[str, str, str, float, float]] = []
    cats = cfg.categories if category_index is None else [cfg.categories[category_index]]

    price_cols = cols & {"buy_price", "sell_price"}
    plain_cols = [c for c in _NUMERIC_PRODUCT_FIELDS if c in cols and c not in price_cols]

    for cat in cats:
        for product in cat.products:
            if product.unreadable:
                continue
            if price_cols:
                old_buy, old_sell = product.buy_price, product.sell_price
                new_buy, new_sell = apply_price_multiplier(old_buy, old_sell, multiplier)
                # Only the picked columns are written, so "sell <= buy" holds
                # against the buy price that stays: a scaled sell price meets
                # the stored buy price, and a lowered buy price pulls the
                # stored sell price down with it.
                if "buy_price" not in price_cols:
                    new_buy = old_buy
                if "sell_price" not in price_cols:
                    new_sell = old_sell
                if new_buy >= 1 and new_sell > new_buy:
                    new_sell = new_buy
                if new_buy != old_buy:
                    product.buy_price = new_buy
                    changes.append((product.classname, cat.name, "buy_price", old_buy, new_buy))
                if new_sell != old_sell:
                    product.sell_price = new_sell
                    changes.append((product.classname, cat.name, "sell_price", old_sell, new_sell))
            for col in plain_cols:
                old = getattr(product, col, None)
                if old is None:
                    continue
                if col == "max_stock" and old < 0:
                    continue  # sentinel: -1 unlimited / -2 auto
                new = old * multiplier
                if col == "max_stock":
                    new = int(round(new))
                if new != old:
                    setattr(product, col, new)
                    changes.append((product.classname, cat.name, col, float(old), float(new)))

    return changes


def apply_batch_set(
    cat: TraderPlusCategory,
    field: str,
    value: float | int,
) -> int:
    """Set ``field`` on every product in ``cat`` to ``value``. Returns count changed.

    ``buy_price`` / ``sell_price`` keep their existing -1 (disabled) value
    so opt-out products remain disabled. ``max_stock`` is cast to int.
    """
    if field not in _NUMERIC_PRODUCT_FIELDS:
        raise ValueError(f"Unknown product field: {field!r}")

    n = 0
    for product in cat.products:
        if product.unreadable:
            continue
        if field == "max_stock":
            new_value: float | int = int(round(value))
        else:
            new_value = float(value)
        old = getattr(product, field, None)
        if old is None:
            continue
        if field in ("buy_price", "sell_price") and old == -1:
            continue  # leave disabled rows alone
        if old == new_value:
            continue
        setattr(product, field, new_value)
        n += 1
    return n



# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def find_product(cfg: TraderPlusPriceConfig, classname: str) -> list[tuple[int, int, TraderPlusProduct]]:
    """Find all products matching classname (case-insensitive).

    Returns list of (category_index, product_index, product).
    """
    results: list[tuple[int, int, TraderPlusProduct]] = []
    needle = classname.lower()
    for ci, cat in enumerate(cfg.categories):
        for pi, prod in enumerate(cat.products):
            if prod.classname.lower() == needle:
                results.append((ci, pi, prod))
    return results


