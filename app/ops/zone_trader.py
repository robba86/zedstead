"""A zone mod's items as a TraderPlus or Expansion Market category (plans/48, D).

RadZone and ToxicZone add items (suits, masks, cures, grenades) that no
TraderPlus trader sells until someone writes them into
``TraderPlusPriceConfig.json`` and gives the category to a trader in
``TraderPlusIDsConfig.json``. This builds that category from the classes the
installed mod's own types file lists, then writes both files.

**Class names, and prices only when the admin asks** (D10). Both mods' terms
forbid "rework or re use" of the author's work without consent, so the
category is written unpriced (every price -1) unless the admin asks. For
ToxicZone only, the admin can tick "use the mod's prices":
they are read from the ``Trader.txt`` in the admin's *installed* copy of the
mod, never from zsm's source, which carries nothing from either mod.
RadZone's items stay unpriced (D10). plans/48 finding 7 has the terms.

A class is left out, and the reason reported, when:

- the last boot that loaded the mod dropped it
  (``log_analysis.rejected_classes``). The PBO class scan cannot stand in: it
  reads only each PBO's root ``config.bin`` and finds none of either mod's
  classes;
- the price file already lists it, in any category, so a second click adds
  nothing twice and a class the admin already priced keeps its price.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path, PurePath

from app.ops import log_analysis, toxic_zone, zone_mods
from app.ops.mod_files import display_path, installed_mod_name, read_json
from app.ops.zone_mods import ZoneMod

ZONE_MODS: tuple[ZoneMod, ...] = (zone_mods.RADZONE, toxic_zone.TOXICZONE)

#: TraderPlus's trade quantity is the item's own quantity, not a count per
#: trade: its ``CreateInInventory`` fills an item to max below 0, leaves it
#: alone at 0 and sets that amount above 0. Its default price file uses 0 for
#: items without a quantity (NBC gear, the GP5 mask). zsm's product default
#: of 1 would sell a quantity item, such as a pack of pills, with one unit in
#: it. Measured against TraderPlusV1's source and default file, 2026 Q3.
_TRADE_QUANTITY = 0

#: The zone mods whose own ``Trader.txt`` prices the admin may use (D10:
#: ToxicZone only; RadZone's items stay unpriced).
_TRADER_TXT_MODS = frozenset({toxic_zone.TOXICZONE.key})

#: One price row of a DrJones-style ``Trader.txt``: ``Class, quantity, buy,
#: sell``. The quantity (``*`` = full) is not used: TraderPlus's trade
#: quantity means something else (see ``_TRADE_QUANTITY``).
_TRADER_TXT_ROW = re.compile(r"^\s*([A-Za-z0-9_]+)\s*,\s*[^,]*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*$")

_TP_WHERE = "the price file"
_MARKET_WHERE = "the Market"

_REJECTED = {
    "ce_type_missing": "the last boot found no class by this name",
    "ce_type_not_spawnable": "the last boot refused to spawn it: its config scope is not public, "
    "which usually means a base class",
}


def _spec(key: str) -> ZoneMod:
    for spec in ZONE_MODS:
        if spec.key == key:
            return spec
    raise ValueError(f"Unknown zone mod: {key!r}")


def _mod_classes(mod_dir: Path) -> tuple[list[str], list[Path]]:
    """The classes the mod's own types files list, in file order, and the files.

    Both zone mods ship theirs as a bare fragment, ``<type>`` elements with no
    ``<types>`` root, which ``classify_mod_file`` does not recognise; the
    text classifier and a wrapper element take either shape.
    """
    from defusedxml.ElementTree import fromstring

    from app.xml.xml_classify import KIND_TYPES, classify_xml_text
    from app.xml.xml_text import strip_declaration

    names: dict[str, None] = {}
    files: list[Path] = []
    for path in sorted(mod_dir.rglob("*.xml")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if classify_xml_text(text, path.name) != KIND_TYPES:
            continue
        try:
            root = fromstring(f"<fragment>{strip_declaration(text)}</fragment>")
        except Exception:  # defusedxml raises its own errors besides ParseError
            continue
        files.append(path)
        for entry in root.iter("type"):
            name = (entry.get("name") or "").strip()
            if name:
                names.setdefault(name)
    return list(names), files


def _loaded(mods: list[str], installed: str) -> bool:
    want = installed.lower()
    return any(PurePath(m.replace("\\", "/")).name.lower() == want for m in mods)


def _last_boot(config, installed: str) -> tuple[dict[str, str], str | None, str]:
    """(rejected classes, log name, a sentence on what was checked)."""
    log = log_analysis.latest_rpt(config)
    if log is None:
        return {}, None, "No server log yet, so no class was checked against a boot."
    rejected, mods = log_analysis.rejected_classes(log)
    if mods is not None and not _loaded(mods, installed):
        return {}, None, (
            f"The last boot ({log.name}) did not load {installed}, so no class was "
            "checked against the server."
        )
    return rejected, log.name, f"Checked against the last boot ({log.name})."


def _priced(price_cfg) -> dict[str, str]:
    """``{classname lowercased: category}`` for everything the price file lists."""
    seen: dict[str, str] = {}
    if price_cfg is None:
        return seen
    for cat in price_cfg.categories:
        for product in cat.products:
            # A line zsm cannot read still lists its class: don't add it twice.
            seen.setdefault(product.listed_class().lower(), cat.name)
    return seen


def _offer(config, spec: ZoneMod, listed: dict[str, str], where: str) -> dict | None:
    """*spec*'s classes minus the refused ones and those *listed* already.

    *listed* maps a lowercased class to where it is sold ("Medic", "Armbands");
    *where* names the file set in the reason ("the price file", "the Market").
    """
    installed = spec.installed_name(config)
    if installed is None:
        return None
    classes, files = _mod_classes(config.server_dir_path / installed)
    rejected, checked, check_note = _last_boot(config, installed)

    items: list[str] = []
    skipped: list[dict] = []
    for name in classes:
        if name in rejected:
            skipped.append({"classname": name, "reason": _REJECTED[rejected[name]]})
        elif name.lower() in listed:
            skipped.append({"classname": name, "reason": f"already in {where}, in {listed[name.lower()]}"})
        else:
            items.append(name)
    return {
        "key": spec.key,
        "label": spec.label,
        "mod": installed,
        "category": spec.label,
        "sources": [display_path(config, f) for f in files],
        "items": items,
        "skipped": skipped,
        "checked": checked,
        "check_note": check_note,
    }


def _trader_txt(mod_dir: Path) -> Path | None:
    """The mod's own ``Trader.txt``, wherever in its folder it sits."""
    found = sorted(p for p in mod_dir.rglob("*") if p.name.lower() == "trader.txt" and p.is_file())
    return found[0] if found else None


def _trader_txt_prices(path: Path) -> dict[str, dict]:
    """``{class lowercased: {"buy": n, "sell": n}}``; the first row for a class
    wins (a class can appear twice), and a row that is not a price is skipped."""
    prices: dict[str, dict] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return prices
    for line in text.splitlines():
        m = _TRADER_TXT_ROW.match(line)
        if m:
            prices.setdefault(m.group(1).lower(), {"buy": float(m.group(2)), "sell": float(m.group(3))})
    return prices


def _with_prices(config, spec: ZoneMod, offer: dict) -> dict:
    """*offer* plus the prices its mod's own ``Trader.txt`` gives its items.

    ``prices`` is keyed by the offer's own class spelling; ``prices_source``
    names the file, or is None when there are none to use.
    """
    offer["prices"], offer["prices_source"] = {}, None
    if spec.key not in _TRADER_TXT_MODS:
        return offer
    path = _trader_txt(config.server_dir_path / offer["mod"])
    if path is None:
        return offer
    found = _trader_txt_prices(path)
    offer["prices"] = {name: found[name.lower()] for name in offer["items"] if name.lower() in found}
    if offer["prices"]:
        offer["prices_source"] = display_path(config, path)
    return offer


def _all_offers(config, listed: dict[str, str], where: str) -> list[dict]:
    """One offer per installed zone mod, each with its ``Trader.txt`` prices."""
    return [
        _with_prices(config, spec, offer)
        for spec in ZONE_MODS
        if (offer := _offer(config, spec, listed, where)) is not None
    ]


def _traders(config) -> list[dict]:
    """Each trader ID with the NPCs that use it, for the picker."""
    from app.traderplus.traderplus_general_json import get_general_config_path, parse_general_config
    from app.traderplus.traderplus_ids_json import get_ids_config_path, parse_ids_config

    ids_path = get_ids_config_path(config)
    if ids_path is None:
        return []
    npcs: dict[int, list[str]] = {}
    general = get_general_config_path(config)
    if general is not None:
        for npc in parse_general_config(general).traders:
            label = npc.given_name or npc.name
            npcs.setdefault(npc.id, []).append(f"{label} ({npc.role})" if npc.role else label)
    return [
        {"id": entry.id, "names": npcs.get(entry.id, []), "categories": len(entry.categories)}
        for entry in parse_ids_config(ids_path).entries
    ]


def offers(config) -> dict:
    """What the Prices page's dialog shows: one offer per installed zone mod."""
    from app.traderplus.traderplus_json import get_price_config_path, parse_price_config

    price_path = get_price_config_path(config)
    price_cfg = parse_price_config(price_path) if price_path else None
    listed = _priced(price_cfg)
    return {
        "price_file": display_path(config, price_path) if price_path else None,
        "traders": _traders(config),
        "offers": _all_offers(config, listed, _TP_WHERE),
    }


def add_to_traderplus(
    config, key: str, category: str, trader_ids: list[int], use_prices: bool = False,
) -> dict:
    """Add *key*'s offer to the price file as *category* and give it to *trader_ids*.

    The offer is derived again here, never taken from the request, so what is
    written is what the server-side rules allow. With *use_prices*, each item
    the mod's own ``Trader.txt`` prices gets that buy and sell price; the rest
    stay -1. Everything is checked before either file is written; both are
    snapshotted first.
    """
    from app.shared.config_versions import snapshot
    from app.traderplus.traderplus_ids_json import get_ids_config_path, parse_ids_config, save_ids_config
    from app.traderplus.traderplus_json import (
        TraderPlusProduct,
        add_category,
        add_product,
        get_price_config_path,
        parse_price_config,
        save_price_config,
    )

    spec = _spec(key)
    category = category.strip()
    if not category:
        raise ValueError("Name the category.")
    price_path = get_price_config_path(config)
    if price_path is None:
        raise ValueError("TraderPlusPriceConfig.json not found. TraderPlus writes it on its first boot.")
    price_cfg = parse_price_config(price_path)
    offer = _offer(config, spec, _priced(price_cfg), _TP_WHERE)
    if offer is None:
        raise ValueError(f"{spec.label} is not installed, or it is disabled.")
    if not offer["items"]:
        raise ValueError(f"Every {spec.label} item is already in the price file or was refused by the server.")
    prices = _with_prices(config, spec, offer)["prices"] if use_prices else {}
    if use_prices and not prices:
        raise ValueError(f"{spec.label} has no Trader.txt prices Zedstead can use for these items.")

    ids_path = ids_cfg = None
    if trader_ids:
        ids_path = get_ids_config_path(config)
        if ids_path is None:
            raise ValueError("TraderPlusIDsConfig.json not found.")
        ids_cfg = parse_ids_config(ids_path)
        known = {entry.id: entry for entry in ids_cfg.entries}
        unknown = [i for i in trader_ids if i not in known]
        if unknown:
            raise ValueError(f"No trader with ID {', '.join(map(str, unknown))} in TraderPlusIDsConfig.json.")

    snapshot(price_path, trigger="editor")
    target = next((c for c in price_cfg.categories if c.name == category), None)
    created = target is None
    if target is None:
        target = add_category(price_cfg, category)
    for name in offer["items"]:
        price = prices.get(name, {"buy": -1.0, "sell": -1.0})
        add_product(target, TraderPlusProduct(
            classname=name, trade_quantity=_TRADE_QUANTITY, buy_price=price["buy"], sell_price=price["sell"],
        ))
    save_price_config(price_cfg)

    if ids_cfg is not None:
        snapshot(ids_path, trigger="editor")
        for entry in ids_cfg.entries:
            if entry.id in trader_ids and category not in entry.categories:
                entry.categories.append(category)
        save_ids_config(ids_cfg)

    return {
        "category": category,
        "created": created,
        "added": offer["items"],
        "priced": len(prices),
        "skipped": offer["skipped"],
        "traders": sorted(set(trader_ids)),
    }


# ── Expansion Market ──────────────────────────────────────────────────
#
# A Market category is one JSON file in <profiles>/ExpansionMod/Market/ and a
# trader is one in Traders/, which lists category file names as "Name" or
# "Name:mode". Unlike TraderPlus there is no "not for sale" price: every item
# carries a min/max price, so the admin prices each row in the dialog (D13)
# and the request carries those numbers. plans/48, "Package D — Expansion
# Market findings", has the formats and the wiki pages they come from.

MARKET_MOD = ("@DayZ-Expansion-Market", "2572328470")

#: The trader modes Expansion's wiki documents, minus 3 (hidden, attachments
#: only), which a category of wearables has no use for. 1 is the default and
#: is written as the bare name, the way the stock trader files do.
MARKET_MODES = {0: "buy only", 1: "buy and sell", 2: "sell only"}

#: A category is a file name, and traders list it by that name.
_CATEGORY_NAME = re.compile(r"[A-Za-z0-9_]+")


def _expansion_dir(config) -> Path:
    return log_analysis.profiles_dir(config) / "ExpansionMod"


def _json_files(folder: Path) -> list[tuple[Path, dict]]:
    """Every readable JSON object in *folder*, by file name."""
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        doc, _err = read_json(path)
        if doc is not None:
            found.append((path, doc))
    return found


def _market_listed(files: list[tuple[Path, dict]]) -> dict[str, str]:
    """``{class lowercased: category}``; a variant counts as listed, since
    the Market sells it through its base item."""
    listed: dict[str, str] = {}
    for path, doc in files:
        for item in doc.get("Items") or []:
            if not isinstance(item, dict):
                continue
            for name in [item.get("ClassName"), *(item.get("Variants") or [])]:
                if isinstance(name, str) and name:
                    listed.setdefault(name.lower(), path.stem)
    return listed


def _stock_default(files: list[tuple[Path, dict]]) -> dict:
    """The (min, max) stock pair the admin's own Market uses most."""
    pairs = Counter(
        (item.get("MinStockThreshold"), item.get("MaxStockThreshold"))
        for _path, doc in files
        for item in doc.get("Items") or []
        if isinstance(item, dict)
        and isinstance(item.get("MinStockThreshold"), int)
        and isinstance(item.get("MaxStockThreshold"), int)
    )
    (low, high), _n = pairs.most_common(1)[0] if pairs else ((1, 100), 0)
    return {"min": low, "max": high}


def _market_traders(config) -> list[dict]:
    """Each trader file, named by its file name (its DisplayName is usually a
    string-table key, which says nothing on a web page)."""
    traders = []
    for path, doc in _json_files(_expansion_dir(config) / "Traders"):
        name = doc.get("DisplayName")
        shown = [name] if isinstance(name, str) and name and not name.startswith("#") else []
        traders.append({"id": path.stem, "names": shown, "categories": len(doc.get("Categories") or [])})
    return traders


def _market_files(config) -> list[tuple[Path, dict]]:
    """The Market's category files while Expansion Market is installed."""
    if installed_mod_name(config, *MARKET_MOD) is None:
        return []
    return _json_files(_expansion_dir(config) / "Market")


def market_offers(config) -> dict:
    """What the Expansion tab's card shows: one offer per installed zone mod."""
    files = _market_files(config)
    if not files:
        return {"market_dir": None, "traders": [], "stock": None, "modes": MARKET_MODES, "offers": []}
    return {
        "market_dir": display_path(config, files[0][0].parent),
        "traders": _market_traders(config),
        "stock": _stock_default(files),
        "modes": MARKET_MODES,
        # The Trader.txt prices ride along for the dialog's "Fill from
        # Trader.txt" button; the admin still sees and can change every row.
        "offers": _all_offers(config, _market_listed(files), _MARKET_WHERE),
    }


def _market_item(row: dict, allowed: dict[str, str]) -> dict:
    """One requested row as a Market item, or ValueError naming the class."""
    name = str(row.get("classname", ""))
    if name.lower() not in allowed:
        raise ValueError(f"{name or 'A row'} is not one of the items this mod offers.")
    numbers = {}
    for key in ("min_price", "max_price", "min_stock", "max_stock"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name}: {key.replace('_', ' ')} must be a whole number.")
        numbers[key] = value
    if numbers["min_price"] < 1:
        raise ValueError(f"{name}: a price below 1 gives the item away.")
    if numbers["min_price"] > numbers["max_price"]:
        raise ValueError(f"{name}: the lowest price is above the highest.")
    if numbers["min_stock"] < 0 or numbers["min_stock"] > numbers["max_stock"]:
        raise ValueError(f"{name}: the stock range runs backwards or below 0.")
    return {
        # Lowercase, as every item in Expansion's own Market files is.
        "ClassName": allowed[name.lower()].lower(),
        "MaxPriceThreshold": numbers["max_price"],
        "MinPriceThreshold": numbers["min_price"],
        "SellPricePercent": -1.0,  # the zone's setting
        "MaxStockThreshold": numbers["max_stock"],
        "MinStockThreshold": numbers["min_stock"],
        "QuantityPercent": -1,
        "SpawnAttachments": [],
        "Variants": [],
    }


def add_to_market(
    config, key: str, category: str, trader_ids: list[str], mode: int, rows: list[dict],
) -> dict:
    """Write *rows* of *key*'s offer as Market category *category* and list it
    at *trader_ids* in *mode*.

    The offer is re-derived and every row checked against it, so a request can
    price only the items this mod offers. A new category file takes its
    version and cosmetics from the first existing one, which is the admin's own
    install rather than a number zsm would have to keep current. Everything is
    checked before any file is written.
    """
    from app.shared.config_versions import snapshot
    from app.shared.json_patch import save_json_patched

    spec = _spec(key)
    category = category.strip()
    if not _CATEGORY_NAME.fullmatch(category):
        raise ValueError("Name the category with letters, digits and _ only; it becomes a file name.")
    if mode not in MARKET_MODES:
        raise ValueError(f"Unknown trader mode: {mode}.")
    files = _market_files(config)
    if not files:
        raise ValueError("Expansion Market is not installed, or has not written its Market files yet.")
    offer = _offer(config, spec, _market_listed(files), _MARKET_WHERE)
    if offer is None:
        raise ValueError(f"{spec.label} is not installed, or it is disabled.")

    allowed = {name.lower(): name for name in offer["items"]}
    items = [_market_item(row, allowed) for row in rows]
    if not items:
        raise ValueError("Price at least one item.")
    names = [i["ClassName"] for i in items]
    if len(set(names)) != len(names):
        raise ValueError("An item is listed twice.")

    known = {t["id"] for t in _market_traders(config)}
    unknown = [t for t in trader_ids if t not in known]
    if unknown:
        raise ValueError(f"No trader file named {', '.join(unknown)}.")
    trader_dir = _expansion_dir(config) / "Traders"
    trader_docs = {t: read_json(trader_dir / f"{t}.json")[0] for t in dict.fromkeys(trader_ids)}

    market_dir = files[0][0].parent
    target = market_dir / f"{category}.json"
    existing = next((doc for path, doc in files if path == target), None)
    if target.exists() and existing is None:
        raise ValueError(f"{target.name} exists but is not readable JSON; fix it first.")
    if existing is None:
        template = files[0][1]
        doc = {
            "m_Version": template.get("m_Version"),
            "DisplayName": category.replace("_", " "),
            "Icon": template.get("Icon", ""),
            "Color": template.get("Color", ""),
            "IsExchange": 0,
            "InitStockPercent": template.get("InitStockPercent", 75.0),
            "Items": items,
        }
    else:
        doc = existing
        doc.setdefault("Items", []).extend(items)
        snapshot(target, trigger="editor")
    save_json_patched(target, doc)

    entry = category if mode == 1 else f"{category}:{mode}"
    for trader_id, trader in trader_docs.items():
        path = trader_dir / f"{trader_id}.json"
        cats = trader.setdefault("Categories", [])
        if any(str(c).split(":")[0].lower() == category.lower() for c in cats):
            continue
        snapshot(path, trigger="editor")
        cats.append(entry)
        save_json_patched(path, trader)

    return {
        "category": category,
        "created": existing is None,
        "added": [allowed[n] for n in names],
        "skipped": offer["skipped"],
        "traders": sorted(set(trader_ids)),
    }
