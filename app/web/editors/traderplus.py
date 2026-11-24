"""TraderPlus config adapters.

banking is a flat form; ids/vehicles/insurance are tables whose save fully
replaces the list (safe here — that's exactly how the underlying
save_*_config functions already work, patching only that one array back into
the preserved `_raw` JSON).

general/safezone/garage carry heavier nested list-of-object shapes (traders,
safe-area locations, NPCs). They were text-kind for that reason and stopped
being so in `0e24abd` (2026 Q3): **all seven are form or table today**, and
banking, safezone and garage additionally have a designed view in
`webui/src/interactiveViews.jsx`. This docstring still described the old shape
in 2026 Q3, which is the first thing anyone reads before touching the file —
re-check `kind=` at the registrations below before trusting a summary up here.
"""

from __future__ import annotations

import re

from app.shared.config import DayzConfig
from app.shared.config_versions import snapshot
from app.web.editors import EditorSpec, register
from app.web.editors._mod_json import (
    F,
    Field,
    Form,
    coerce,
    coerce_list,
    form_load,
    form_save,
    list_field,
    shown,
)
from app.web.editors._raw import raw_targets_from
from app.web.editors._help import unit_from_label


# ---------------------------------------------------------------------------
# Lists of names (plans/76): a real array on the wire, never a joined string
# ---------------------------------------------------------------------------


def _list_column(key: str, label: str) -> dict:
    return {"key": key, "label": label, "type": "list", "item": "str"}


def _pick_field(key: str, label: str, value: list) -> dict:
    """A form field for a list of names, shown as a pick list.

    ``widget: "pick"`` makes FormEditor show it as a MultiSelectList (a filter,
    ticks and Remove selected) rather than one box per item: these lists are
    class names and Steam IDs, often dozens of them. It was the TraderPlus
    Interactive tab's view, which went in 2026 Q4 when the Edit tab took it.
    """
    return {**list_field(key, label, value, "str"), "widget": "pick"}


def _names(raw, current, label: str) -> list:
    """The list the editor sent for a list of names, checked.

    The page sends an array (``coerce_list``: an item left as it was stays
    exactly as the file had it, and a value may hold a comma). A tab opened
    before the lists became arrays still sends the old comma-joined string,
    which is read the old way so that its save keeps working.
    """
    if isinstance(raw, str):
        return [v.strip() for v in raw.split(",") if v.strip()]
    try:
        return coerce_list(raw, "str", current)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc


# Fields that hold a 0.0-1.0 share. A min and max make the form draw them as
# a 0-100 % slider (webui RangeField sliderScale); the file keeps 0.0-1.0.
# The item-state coefficients are price multipliers shown the same way, as the
# insurance table's are (_insurance_load).
_SHARE_FIELDS = frozenset({
    "lock_pick_chance", "transaction_fees",
    "state:coefficient_worn", "state:coefficient_damaged", "state:coefficient_badly_damaged",
})


def _scalar_field(key: str, label: str, kind: str, value) -> dict:
    """A form field showing *value* the way every mod form does
    (``_mod_json.shown``): a float as the shortest decimal DayZ reads the same,
    so ``0.05000000074505806`` shows as ``0.05``. A label's "(s)" becomes the
    box's unit (``unit_from_label``)."""
    shown_label, unit = unit_from_label(label)
    share = {"min": 0, "max": 1} if key in _SHARE_FIELDS else {}
    return {"key": key, "label": shown_label, "type": kind, "value": shown(Field(key, label, kind), value), **unit, **share}


# Fields that hold an amount of money: the box shows the server's currency.
_MONEY_FIELDS = frozenset({"park_in_cost", "park_out_cost", "default_start_currency", "default_max_currency"})


def _currency_unit(config: DayzConfig) -> dict:
    """``{"unit": ...}`` for a money field: TraderPlus counts every amount in
    the value of its cheapest currency (General's Currencies, MoneyRuble1 at
    1), so that currency's short name (``_currency_name``) is the unit.
    ``{}`` when the general config is missing, unreadable or lists no
    currency, so the box shows no unit rather than a wrong one."""
    from app.traderplus.traderplus_general_json import get_general_config_path, parse_general_config

    path = get_general_config_path(config)
    if path is None:
        return {}
    try:
        currencies = [c for c in parse_general_config(path).currencies if c.classname]
    except (OSError, ValueError):
        return {}
    if not currencies:
        return {}
    cheapest = min(currencies, key=lambda c: c.value)
    return {"unit": _currency_name(cheapest.classname)}


def _currency_name(classname: str) -> str:
    """A short name for a Currencies ClassName, to fit a box's unit. The entry
    can list several items ("TraderPlus_Money_Ruble1_Coin,TraderPlus_Money_Euro1"),
    so the first one names it. Its count and anything after the count go
    ("_Coin"), then everything up to the last underscore and a leading
    "Money": "TraderPlus_Money_Ruble1_Coin" and "MoneyRuble1" are both
    "Ruble". A name that would come out empty is kept as it was."""
    first = classname.split(",")[0].strip() or classname
    base = re.split(r"_?\d", first, maxsplit=1)[0] or first
    word = base.rsplit("_", 1)[-1] or base
    return re.sub(r"^(?:TraderPlus)?Money(?=[A-Z])", "", word) or word


def _with_currency(fields: list[dict], config: DayzConfig) -> list[dict]:
    """*fields* with the currency unit on each money field (``_MONEY_FIELDS``)."""
    unit = _currency_unit(config) if any(f["key"] in _MONEY_FIELDS for f in fields) else {}
    return [{**f, **unit} if f["key"] in _MONEY_FIELDS else f for f in fields]


def _typed(kind: str, label: str, raw, current):
    """*raw* from the page, typed the way TraderPlus loads a field of *kind*.

    ``_mod_json.coerce`` is the rule every mod form follows: an ``int`` field
    refuses ``4.5`` with the field named (``int()`` cut it to 4), a ``bool``
    is written as the mod's 0/1 (``bool("false")`` was True), and a ``float``
    still showing the file's value keeps the file's digits.
    """
    try:
        return coerce(Field("", label, kind), raw, current)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc

# ---------------------------------------------------------------------------
# Banking (form)
# ---------------------------------------------------------------------------


_BANKING_SCALARS = [
    ("banking_logs", "Banking logs", "bool"),
    ("is_credit_car_needed", "Credit card required for transactions", "bool"),
    ("transaction_fees", "Transaction fees", "float"),
    ("default_start_currency", "Default start currency", "int"),
    ("default_max_currency", "Default max currency", "int"),
]


def _banking_path(config: DayzConfig):
    from app.traderplus.traderplus_banking_json import get_banking_config_path

    return get_banking_config_path(config)


def _banking_exists(config: DayzConfig) -> bool:
    return _banking_path(config) is not None


def _banking_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_banking_json import get_banking_config_path, parse_banking_config

    path = get_banking_config_path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    cfg = parse_banking_config(path)
    fields = [
        _scalar_field(attr, label, kind, getattr(cfg, attr)) for attr, label, kind in _BANKING_SCALARS
    ] + [
        _pick_field("currencies_accepted", "Currencies accepted", cfg.currencies_accepted),
        {"key": "transfer_message", "label": "Transfer message", "type": "str", "value": cfg.transfer_message},
    ]
    return {"kind": "form", "sections": [{"title": "TraderPlus Banking", "fields": _with_currency(fields, config)}]}


def _banking_save(config: DayzConfig, values: dict) -> None:
    from app.traderplus.traderplus_banking_json import (
        get_banking_config_path,
        parse_banking_config,
        save_banking_config,
    )

    path = get_banking_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusBankingConfig.json not found")
    cfg = parse_banking_config(path)
    for attr, label, kind in _BANKING_SCALARS:
        if attr in values:
            setattr(cfg, attr, _typed(kind, label, values[attr], getattr(cfg, attr)))
    if "currencies_accepted" in values:
        cfg.currencies_accepted = _names(
            values["currencies_accepted"], cfg.currencies_accepted, "Currencies accepted"
        )
    if "transfer_message" in values:
        cfg.transfer_message = str(values["transfer_message"])
    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    save_banking_config(cfg)


register(
    EditorSpec(
        id="tp-banking",
        title="TraderPlus Banking",
        group="TraderPlus",
        kind="form",
        exists=_banking_exists,
        load=_banking_load,
        save=_banking_save,
        raw_targets=raw_targets_from((_banking_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# IDs (table)
# ---------------------------------------------------------------------------


_IDS_LISTS = (
    ("categories", "Categories"),
    ("licences_required", "Licences required"),
    ("currencies_accepted", "Currencies accepted"),
)


def _ids_path(config: DayzConfig):
    from app.traderplus.traderplus_ids_json import get_ids_config_path

    return get_ids_config_path(config)


def _ids_exists(config: DayzConfig) -> bool:
    return _ids_path(config) is not None


def _dup_row_ids(keys) -> list[str]:
    """One row id per entry from its key: the key, and ``<key>-dup-<n>`` for a
    later entry with the same key.

    Two entries with one key (a trader Id, a vehicle name) shared the row id,
    so removing one row in the table removed both, the real trader's
    categories with it. A file written that way
    before the save refused it still loads, and each row stays its own.
    """
    seen: dict[str, int] = {}
    ids = []
    for key in map(str, keys):
        n = seen.get(key, 0)
        seen[key] = n + 1
        ids.append(key if n == 0 else f"{key}-dup-{n}")
    return ids


def _ids_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_ids_json import get_ids_config_path, parse_ids_config

    path = get_ids_config_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    cfg = parse_ids_config(path)
    columns = [
        {"key": "id", "label": "ID", "type": "int"},
        _list_column("categories", "Categories"),
        _list_column("licences_required", "Licences required"),
        _list_column("currencies_accepted", "Currencies accepted"),
    ]
    rows = [
        {
            "id": row_id,
            "values": {
                "id": e.id,
                "categories": list(e.categories),
                "licences_required": list(e.licences_required),
                "currencies_accepted": list(e.currencies_accepted),
            },
        }
        for row_id, e in zip(_dup_row_ids(e.id for e in cfg.entries), cfg.entries)
    ]
    return {"kind": "table", "columns": columns, "rows": rows}


def _ids_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.traderplus.traderplus_ids_json import (
        TraderIdEntry,
        get_ids_config_path,
        parse_ids_config,
        save_ids_config,
    )

    path = get_ids_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusIDsConfig.json not found")
    cfg = parse_ids_config(path)
    # A row's id is its entry's row id as loaded; a list cell compares against it.
    loaded = dict(zip(_dup_row_ids(e.id for e in cfg.entries), cfg.entries))
    entries = []
    for row in rows:
        values = row.get("values", row)
        was = loaded.get(str(row.get("id")))
        lists = {}
        for key, label in _IDS_LISTS:
            current = getattr(was, key) if was else []
            lists[key] = _names(values.get(key, current), current, label)
        entries.append(
            TraderIdEntry(
                id=_typed("int", "ID", values.get("id", 0), was.id if was else 0),
                **lists,
                raw=was.raw if was else {},
            )
        )
    # TraderPlus maps one trader ID to one entry: a second row with an ID in
    # use was written, and its categories never reached a trader.
    taken: set[int] = set()
    for e in entries:
        if e.id in taken:
            raise ValueError(f"Two rows have ID {e.id}. Each trader ID has one entry, so give one of them another ID.")
        taken.add(e.id)
    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    cfg.entries = entries
    save_ids_config(cfg)


register(
    EditorSpec(
        id="tp-ids",
        title="TraderPlus IDs",
        group="TraderPlus",
        kind="table",
        exists=_ids_exists,
        load=_ids_load,
        save=_ids_save,
        raw_targets=raw_targets_from((_ids_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# Vehicles (table)
# ---------------------------------------------------------------------------


def _vehicles_path(config: DayzConfig):
    from app.traderplus.traderplus_vehicles_json import get_vehicles_config_path

    return get_vehicles_config_path(config)


def _vehicles_exists(config: DayzConfig) -> bool:
    return _vehicles_path(config) is not None


def _vehicles_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_vehicles_json import get_vehicles_config_path, parse_vehicles_config

    path = get_vehicles_config_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    cfg = parse_vehicles_config(path)
    columns = [
        {"key": "vehicle_name", "label": "Vehicle", "type": "str"},
        {"key": "height", "label": "Height", "type": "int", "unit": "meters"},
        _list_column("vehicle_parts", "Parts"),
    ]
    rows = [
        {
            "id": row_id,
            "values": {
                "vehicle_name": v.vehicle_name,
                "height": v.height,
                "vehicle_parts": list(v.vehicle_parts),
            },
        }
        for row_id, v in zip(_dup_row_ids(v.vehicle_name for v in cfg.vehicles), cfg.vehicles)
    ]
    return {"kind": "table", "columns": columns, "rows": rows}


def _vehicles_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.traderplus.traderplus_vehicles_json import (
        VehicleDefinition,
        get_vehicles_config_path,
        parse_vehicles_config,
        save_vehicles_config,
    )

    path = get_vehicles_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusVehiclesConfig.json not found")
    cfg = parse_vehicles_config(path)
    # A row's id is its vehicle's row id as loaded; the parts compare against
    # it, and the entry keeps the keys the table does not show.
    loaded = dict(zip(_dup_row_ids(v.vehicle_name for v in cfg.vehicles), cfg.vehicles))
    vehicles = []
    for row in rows:
        values = row.get("values", row)
        was = loaded.get(str(row.get("id")))
        current = was.vehicle_parts if was else []
        vehicles.append(
            VehicleDefinition(
                vehicle_name=values.get("vehicle_name", ""),
                height=_typed("int", "Height", values.get("height", 0), was.height if was else 0),
                vehicle_parts=_names(values.get("vehicle_parts", current), current, "Parts"),
                raw=was.raw if was else {},
            )
        )
    names = [v.vehicle_name for v in vehicles]
    dup = next((n for i, n in enumerate(names) if n in names[:i]), None)
    if dup is not None:
        raise ValueError(f"Two rows are vehicle {dup}. TraderPlus reads one entry per vehicle, so remove one.")
    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    cfg.vehicles = vehicles
    save_vehicles_config(cfg)


register(
    EditorSpec(
        id="tp-vehicles",
        title="TraderPlus Vehicles",
        group="TraderPlus",
        kind="table",
        exists=_vehicles_exists,
        load=_vehicles_load,
        save=_vehicles_save,
        raw_targets=raw_targets_from((_vehicles_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# Insurance (table — Insurances list; AuthorizedIDInsurance list not exposed)
# ---------------------------------------------------------------------------


def _insurance_path(config: DayzConfig):
    from app.traderplus.traderplus_insurance_json import get_insurance_config_path

    return get_insurance_config_path(config)


def _insurance_exists(config: DayzConfig) -> bool:
    return _insurance_path(config) is not None


def _insurance_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_insurance_json import get_insurance_config_path, parse_insurance_config

    path = get_insurance_config_path(config)
    if path is None:
        return {"kind": "table", "columns": [], "rows": []}
    cfg = parse_insurance_config(path)
    columns = [
        {"key": "vehicle_name", "label": "Vehicle", "type": "str"},
        # Multipliers of the vehicle price, typed as a percentage (webui
        # ShareInput). They can pass 100 %, so the column has no max.
        {"key": "insurance_price_coefficient", "label": "Insurance price coef.", "type": "float", "share": True},
        {"key": "collateral_money_coefficient", "label": "Collateral coef.", "type": "float", "share": True},
    ]
    rows = [
        {
            "id": row_id,
            "values": {
                "vehicle_name": e.vehicle_name,
                "insurance_price_coefficient": e.insurance_price_coefficient,
                "collateral_money_coefficient": e.collateral_money_coefficient,
            },
        }
        for row_id, e in zip(_dup_row_ids(e.vehicle_name for e in cfg.insurances), cfg.insurances)
    ]
    return {"kind": "table", "columns": columns, "rows": rows}


def _insurance_save(config: DayzConfig, rows: list[dict]) -> None:
    from app.traderplus.traderplus_insurance_json import (
        InsuranceEntry,
        get_insurance_config_path,
        parse_insurance_config,
        save_insurance_config,
    )

    path = get_insurance_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusInsuranceConfig.json not found")
    cfg = parse_insurance_config(path)
    # A row's id is its vehicle's row id as loaded; the entry keeps the keys
    # the table does not show.
    loaded = dict(zip(_dup_row_ids(e.vehicle_name for e in cfg.insurances), cfg.insurances))
    entries = []
    for row in rows:
        values = row.get("values", row)
        was = loaded.get(str(row.get("id")))
        entries.append(
            InsuranceEntry(
                vehicle_name=values.get("vehicle_name", ""),
                insurance_price_coefficient=_typed(
                    "float", "Insurance price coef.", values.get("insurance_price_coefficient", 1.25),
                    was.insurance_price_coefficient if was else None,
                ),
                collateral_money_coefficient=_typed(
                    "float", "Collateral coef.", values.get("collateral_money_coefficient", 0.75),
                    was.collateral_money_coefficient if was else None,
                ),
                raw=was.raw if was else {},
            )
        )
    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    cfg.insurances = entries
    save_insurance_config(cfg)


register(
    EditorSpec(
        id="tp-insurance",
        title="TraderPlus Insurance",
        group="TraderPlus",
        kind="table",
        exists=_insurance_exists,
        load=_insurance_load,
        save=_insurance_save,
        raw_targets=raw_targets_from((_insurance_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# General (form — toggles/scalars/licences/accepted states; currencies,
# trader NPCs and TraderObjects are preserved untouched in _raw)
# ---------------------------------------------------------------------------

_GENERAL_TOGGLES = [
    ("convert_trader_config", "Convert TraderConfig to TraderPlus"),
    ("convert_trader_config_stock_ce", "Convert with stock based on CE"),
    ("use_garage_to_trade_car", "Use garage to trade cars"),
    ("disable_height_failsafe", "Disable height failsafe (receipt deploy)"),
    ("hide_insurance_btn", "Hide insurance button"),
    ("hide_garage_btn", "Hide garage button"),
    ("hide_licence_btn", "Hide licence button"),
    ("enable_show_all_prices", "Show all prices"),
    ("enable_show_all_checkbox", "Show-all checkbox"),
    ("is_receipt_trader_only", "Receipt trader only"),
    ("is_receipt_save_lock", "Receipt saves lock"),
    ("is_receipt_save_attachment", "Receipt saves attachments"),
    ("is_receipt_save_cargo", "Receipt saves cargo"),
    ("store_only_to_pristine_state", "Store only to pristine state"),
]

_GENERAL_STATE_FIELDS = [
    ("accept_worn", "Accept worn", "bool"),
    ("accept_damaged", "Accept damaged", "bool"),
    ("accept_badly_damaged", "Accept badly damaged", "bool"),
    ("coefficient_worn", "Coefficient worn", "float"),
    ("coefficient_damaged", "Coefficient damaged", "float"),
    ("coefficient_badly_damaged", "Coefficient badly damaged", "float"),
]


def _general_path(config: DayzConfig):
    from app.traderplus.traderplus_general_json import get_general_config_path

    return get_general_config_path(config)


def _general_exists(config: DayzConfig) -> bool:
    return _general_path(config) is not None


def _general_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_general_json import get_general_config_path, parse_general_config

    path = get_general_config_path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    cfg = parse_general_config(path)
    toggle_fields = [
        _scalar_field(attr, label, "bool", getattr(cfg, attr)) for attr, label in _GENERAL_TOGGLES
    ]
    scalar_fields = [
        _scalar_field("lock_pick_chance", "Lock pick chance", "float", cfg.lock_pick_chance),
        {"key": "licence_keyword", "label": "Licence keyword", "type": "str", "value": cfg.licence_keyword},
        _pick_field("licences", "Licences", cfg.licences),
    ]
    state_fields = [
        _scalar_field(f"state:{attr}", label, kind, getattr(cfg.accepted_states, attr))
        for attr, label, kind in _GENERAL_STATE_FIELDS
    ]
    return {
        "kind": "form",
        "sections": [
            {"title": "Toggles", "fields": toggle_fields},
            {"title": "General", "fields": scalar_fields},
            {"title": "Accepted item states", "fields": state_fields},
        ],
    }


def _general_save(config: DayzConfig, values: dict) -> None:
    from app.traderplus.traderplus_general_json import (
        get_general_config_path,
        parse_general_config,
        save_general_config,
    )

    path = get_general_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusGeneralConfig.json not found")
    cfg = parse_general_config(path)
    for attr, label in _GENERAL_TOGGLES:
        if attr in values:
            setattr(cfg, attr, _typed("bool", label, values[attr], getattr(cfg, attr)))
    if "lock_pick_chance" in values:
        cfg.lock_pick_chance = _typed(
            "float", "Lock pick chance", values["lock_pick_chance"], cfg.lock_pick_chance
        )
    if "licence_keyword" in values:
        cfg.licence_keyword = str(values["licence_keyword"])
    if "licences" in values:
        cfg.licences = _names(values["licences"], cfg.licences, "Licences")
    for attr, label, kind in _GENERAL_STATE_FIELDS:
        key = f"state:{attr}"
        if key in values:
            states = cfg.accepted_states
            setattr(states, attr, _typed(kind, label, values[key], getattr(states, attr)))
    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    save_general_config(cfg)


register(
    EditorSpec(
        id="tp-general",
        title="TraderPlus General",
        group="TraderPlus",
        kind="form",
        exists=_general_exists,
        load=_general_load,
        save=_general_save,
        raw_targets=raw_targets_from((_general_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# SafeZone (form — scalars/messages/lists plus one section per safe area;
# adding/removing areas is done in the Visual Map Editor or raw file)
# ---------------------------------------------------------------------------

_SAFEZONE_SCALARS = [
    ("is_hideout_active", "Hideout active", "bool"),
    ("enable_afk_disconnect", "AFK disconnect", "bool"),
    ("kick_after_delay", "Kick after delay (min)", "int"),
    ("cleanup_timer", "Cleanup timer (s)", "int"),
]

_SAFEZONE_MESSAGES = [
    ("msg_enter_zone", "Enter-zone message"),
    ("msg_exit_zone", "Exit-zone message"),
    ("msg_on_leaving_zone", "Leaving-zone message"),
    ("must_remove_armband", "Must-remove-armband message"),
]

_SAFEZONE_LISTS = [
    ("whitelist_entities", "Whitelisted entities"),
    ("objects_to_delete", "Objects to delete"),
    ("sz_steam_uids", "Admin Steam UIDs"),
    ("blacklisted_item_in_stash", "Blacklisted stash items"),
]

_SAFEZONE_AREA_FIELDS = [
    ("name", "Name", "str"),
    ("x", "X", "float"),
    ("y", "Y (Z world coord)", "float"),
    ("radius", "Radius (m)", "float"),
    ("countdown", "Countdown (s)", "int"),
]

#: An area field's attribute on ``SafeAreaLocation``, where it is not its key.
_SAFEZONE_AREA_ATTRS = {"name": "safe_zone_statut"}


def _safezone_path(config: DayzConfig):
    from app.traderplus.traderplus_safezone_json import get_safezone_config_path

    return get_safezone_config_path(config)


def _safezone_exists(config: DayzConfig) -> bool:
    return _safezone_path(config) is not None


def _safezone_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_safezone_json import get_safezone_config_path, parse_safezone_config

    path = get_safezone_config_path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    cfg = parse_safezone_config(path)
    scalar_fields = [
        _scalar_field(attr, label, kind, getattr(cfg, attr)) for attr, label, kind in _SAFEZONE_SCALARS
    ]
    message_fields = [
        {"key": attr, "label": label, "type": "str", "value": getattr(cfg, attr)}
        for attr, label in _SAFEZONE_MESSAGES
    ]
    list_fields = [_pick_field(attr, label, getattr(cfg, attr)) for attr, label in _SAFEZONE_LISTS]
    sections = [
        {"title": "SafeZone", "fields": scalar_fields},
        {"title": "Messages", "fields": message_fields},
        {"title": "Lists", "fields": list_fields},
    ]
    for i, area in enumerate(cfg.safe_area_locations):
        values = {
            "name": area.safe_zone_statut,
            "x": area.x,
            "y": area.y,
            "radius": area.radius,
            "countdown": area.countdown,
        }
        sections.append(
            {
                "title": f"Safe area {i + 1}: {area.safe_zone_statut or '(unnamed)'}",
                "fields": [
                    _scalar_field(f"area:{i}:{key}", label, kind, values[key])
                    for key, label, kind in _SAFEZONE_AREA_FIELDS
                ],
            }
        )
    return {"kind": "form", "sections": sections}


def _safezone_save(config: DayzConfig, values: dict) -> None:
    from app.traderplus.traderplus_safezone_json import (
        get_safezone_config_path,
        parse_safezone_config,
        save_safezone_config,
    )

    path = get_safezone_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusSafeZoneConfig.json not found")
    cfg = parse_safezone_config(path)

    for attr, label, kind in _SAFEZONE_SCALARS:
        if attr in values:
            setattr(cfg, attr, _typed(kind, label, values[attr], getattr(cfg, attr)))
    for attr, _label in _SAFEZONE_MESSAGES:
        if attr in values:
            setattr(cfg, attr, str(values[attr]))
    for attr, label in _SAFEZONE_LISTS:
        if attr in values:
            setattr(cfg, attr, _names(values[attr], getattr(cfg, attr), label))

    areas = cfg.safe_area_locations
    fields = {key: (label, kind) for key, label, kind in _SAFEZONE_AREA_FIELDS}
    for key, value in values.items():
        if not key.startswith("area:"):
            continue
        _, idx_str, field_name = key.split(":", 2)
        idx = int(idx_str)
        if field_name not in fields or not 0 <= idx < len(areas):
            continue
        label, kind = fields[field_name]
        attr = _SAFEZONE_AREA_ATTRS.get(field_name, field_name)
        current = getattr(areas[idx], attr)
        setattr(areas[idx], attr, _typed(kind, f"Safe area {idx + 1}: {label}", value, current))

    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    save_safezone_config(cfg)


register(
    EditorSpec(
        id="tp-safezone",
        title="TraderPlus SafeZone",
        group="TraderPlus",
        kind="form",
        exists=_safezone_exists,
        load=_safezone_load,
        save=_safezone_save,
        raw_targets=raw_targets_from((_safezone_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# Garage (form — toggles/costs/whitelist; NPC placements are preserved
# untouched in _raw and edited via the Visual Map Editor)
# ---------------------------------------------------------------------------

_GARAGE_TOGGLES = [
    ("use_garage_only_to_trade", "Use garage only to trade"),
    ("vehicle_must_have_lock", "Vehicle must have lock"),
    ("save_vehicle_cargo", "Save vehicle cargo"),
    ("save_vehicle_health", "Save vehicle health"),
    ("save_vehicle_fuel", "Save vehicle fuel"),
    ("pay_with_bank_account", "Pay with bank account"),
]

_GARAGE_NUMBERS = [
    ("saved_vehicle_in_garage_for_trade_in_hour", "Trade-in availability (hours)"),
    ("max_vehicle_stored", "Max vehicles stored"),
    ("park_in_cost", "Park-in cost"),
    ("park_out_cost", "Park-out cost"),
]


def _garage_path(config: DayzConfig):
    from app.traderplus.traderplus_garage_json import get_garage_config_path

    return get_garage_config_path(config)


def _garage_exists(config: DayzConfig) -> bool:
    return _garage_path(config) is not None


def _garage_load(config: DayzConfig) -> dict:
    from app.traderplus.traderplus_garage_json import get_garage_config_path, parse_garage_config

    path = get_garage_config_path(config)
    if path is None:
        return {"kind": "form", "sections": []}
    cfg = parse_garage_config(path)
    toggle_fields = [
        _scalar_field(attr, label, "bool", getattr(cfg, attr)) for attr, label in _GARAGE_TOGGLES
    ]
    number_fields = [
        _scalar_field(attr, label, "int", getattr(cfg, attr)) for attr, label in _GARAGE_NUMBERS
    ]
    list_fields = [
        _pick_field("whitelisted_objects", "Whitelisted objects", cfg.whitelisted_objects),
        {
            "key": "npc_count",
            "label": f"Garage NPCs (read-only — {len(cfg.npcs)} placed via Map Editor)",
            "type": "str",
            "value": str(len(cfg.npcs)),
            "readonly": True,
        },
    ]
    return {
        "kind": "form",
        "sections": [
            {"title": "Toggles", "fields": toggle_fields},
            {"title": "Storage & costs", "fields": _with_currency(number_fields, config)},
            {"title": "Lists", "fields": list_fields},
        ],
    }


def _garage_save(config: DayzConfig, values: dict) -> None:
    from app.traderplus.traderplus_garage_json import (
        get_garage_config_path,
        parse_garage_config,
        save_garage_config,
    )

    path = get_garage_config_path(config)
    if path is None:
        raise RuntimeError("TraderPlusGarageConfig.json not found")
    cfg = parse_garage_config(path)
    for attr, label in _GARAGE_TOGGLES:
        if attr in values:
            setattr(cfg, attr, _typed("bool", label, values[attr], getattr(cfg, attr)))
    for attr, label in _GARAGE_NUMBERS:
        if attr in values:
            setattr(cfg, attr, _typed("int", label, values[attr], getattr(cfg, attr)))
    if "whitelisted_objects" in values:
        cfg.whitelisted_objects = _names(
            values["whitelisted_objects"], cfg.whitelisted_objects, "Whitelisted objects"
        )
    snapshot(path, trigger="editor")  # after every check: a refused save keeps no version
    save_garage_config(cfg)


register(
    EditorSpec(
        id="tp-garage",
        title="TraderPlus Garage",
        group="TraderPlus",
        kind="form",
        exists=_garage_exists,
        load=_garage_load,
        save=_garage_save,
        raw_targets=raw_targets_from((_garage_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# Beans Launcher (form): the badge in the trader menu, new in the 2026-11-09
# defaults. ``Info`` is the author's own explanation, never a setting.
# ---------------------------------------------------------------------------

BEANS_FILE = "TraderPlusBeansLauncherConfig.json"


def _beans_path(config: DayzConfig):
    """``TraderPlusConfig/TraderPlusBeansLauncherConfig.json``, or None without the folder."""
    from app.traderplus.trader_common import get_traderplus_config_dir

    cfg_dir = get_traderplus_config_dir(config)
    return cfg_dir / BEANS_FILE if cfg_dir else None


def _beans_exists(config: DayzConfig) -> bool:
    """True when the TraderPlus config folder holds the Beans Launcher file."""
    path = _beans_path(config)
    return path is not None and path.is_file()


BEANS_FORM = Form(
    mod="@TraderPlus",
    path=_beans_path,
    not_on_form=frozenset({"Info"}),
    sections=(
        ("DayZ Beans Launcher badge", (
            F("PartnerCode", "str",
              "Your partner code from dayzbeanslauncher.com. A player who installs the launcher "
              "through the badge earns credits for the server that owns the code. The default "
              "code supports the TraderPlus author.",
              label="Partner code"),
            F("HideBadge", "bool",
              "On: the trader menu shows no Beans Launcher badge.",
              label="Hide badge"),
        )),
    ),
)

register(
    EditorSpec(
        id="tp-beans",
        title="TraderPlus Beans Launcher",
        group="TraderPlus",
        kind="form",
        exists=_beans_exists,
        load=lambda config: form_load(config, BEANS_FORM),
        save=lambda config, values: form_save(config, BEANS_FORM, values),
        raw_targets=raw_targets_from((_beans_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# Field/column help — reuse each editor's FIELD_DESCRIPTIONS, stripped of Rich
# markup, injected into every load() payload.
# ---------------------------------------------------------------------------
from app.traderplus.traderplus_banking_json import FIELD_DESCRIPTIONS as _BANKING_DESC  # noqa: E402
from app.traderplus.traderplus_garage_json import FIELD_DESCRIPTIONS as _GARAGE_DESC  # noqa: E402
from app.traderplus.traderplus_general_json import FIELD_DESCRIPTIONS as _GENERAL_DESC  # noqa: E402
from app.traderplus.traderplus_insurance_json import FIELD_DESCRIPTIONS as _INSURANCE_DESC  # noqa: E402
from app.traderplus.traderplus_safezone_json import FIELD_DESCRIPTIONS as _SAFEZONE_DESC  # noqa: E402
from app.traderplus.traderplus_vehicles_json import FIELD_DESCRIPTIONS as _VEHICLES_DESC  # noqa: E402
from app.web.editors import get_editor  # noqa: E402
from app.web.editors._help import inject_help  # noqa: E402

# ids has no FIELD_DESCRIPTIONS map — web-authored column help.
_IDS_COL_HELP = {
    "id": "Trader NPC id this row configures.",
    "categories": "Category config files this trader offers.",
    "licences_required": "Licences a player must own to trade here.",
    "currencies_accepted": "Currency classnames this trader accepts.",
}

_TP_HELP_MAPS: dict[str, dict[str, str]] = {
    "tp-banking": _BANKING_DESC,
    "tp-ids": _IDS_COL_HELP,
    "tp-vehicles": _VEHICLES_DESC,
    "tp-insurance": _INSURANCE_DESC,
    "tp-general": _GENERAL_DESC,
    "tp-safezone": _SAFEZONE_DESC,
    "tp-garage": _GARAGE_DESC,
}


def _wrap_help(load_fn, desc_map):
    def _load(config: DayzConfig) -> dict:
        return inject_help(load_fn(config), desc_map)

    return _load


for _eid, _desc_map in _TP_HELP_MAPS.items():
    _spec = get_editor(_eid)
    if _spec is not None:
        _spec.load = _wrap_help(_spec.load, _desc_map)
