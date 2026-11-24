"""Every save of a supported mod's config file is lossless.

The same standard Bohemia's files are held to (``app/xml/CLAUDE.md``):

1. a save with no edits leaves the file byte for byte as it was: key order,
   indent, line endings, a BOM and the number text (``1.0``, Enforce's
   17-digit ``0.30000001192092898``) all stay;
2. an edit changes only the edited value, and keys no editor knows stay;
3. a round trip through the editor loses nothing.

Every editor of a supported mod's file is swept here (TraderPlus, RadZone,
ToxicZone, Dynamic AI, Expansion's 30 settings files and its patrol table),
and so are the map editor's layers over the same files.

Every document here is this suite's own made-up content (the mods' authors'
terms forbid copying their files, see ``app/ops/CLAUDE.md``): invented class,
category, trader names, values and positions. The TraderPlus documents keep
the key names and the layout of the files the mod ships in
``@TraderPlus/ServerProfile/TraderPlus/TraderPlusConfig/`` (which zsm seeds
the profile folder with, ``copy_traderplus_default_config``), as Enforce's
``JsonFileLoader`` writes them: 4-space indent, LF, no final newline, 17-digit
floats, 0/1 bools, except TraderPlusIDsConfig.json, whose LF lines end in one
CRLF. The other mods' documents are laid out that way by ``enforce_text``.
"""

from __future__ import annotations

import copy
import difflib
import json
import math
import re
import struct
from pathlib import Path

import pytest

from app.shared import paths
from app.shared.json_utils import SourceFloat, dumps_json
from app.web import editors
from app.web.editors import expansion as exp
from app.web.editors._nested import NESTED, NESTED_BASE
from app.ops import toxic_zone, zone_mods
from tests import test_dynamic_ai as dai
from tests import test_toxic_zone as tzt
from tests import test_zone_mods as rzt
from tests.test_dynamic_ai import dyn  # noqa: F401 - pytest fixture
from tests.test_expansion_ai import eai  # noqa: F401 - pytest fixture
from tests.test_toxic_zone import tzc  # noqa: F401 - pytest fixture
from tests.test_zone_mods import rz  # noqa: F401 - pytest fixture

# ── TraderPlus' files, made up in the mod's layout ──────────────────────

BANKING = """\
{
    "Version": "2.5",
    "BankingLogs": 0,
    "IsCreditCarNeededForTransaction": 0,
    "TransactionFees": 0.15000000596046448,
    "DefaultStartCurrency": 0,
    "DefaultMaxCurrency": 250000,
    "CurrenciesAccepted": [],
    "TheAmountHasBeenTransferedToTheAccount": "Fixture: transfer done.",
    "TheAmountErrorTransferAccount": "Fixture: no such account."
}"""

IDS = """\
{
    "Version": "2.5",
    "IDs": [
        {
            "Id": 0,
            "Categories": [
                "Fixture Permits",
                "Fixture Roots",
                "Fixture Tins",
                "Fixture Bottles",
                "Fixture Bandages"
            ],
            "LicencesRequired": [],
            "CurrenciesAccepted": []
        },
        {
            "Id": 1,
            "Categories": [
                "Fixture Tools",
                "Fixture Ropes"
            ],
            "LicencesRequired": [],
            "CurrenciesAccepted": []
        }
    ]
}""" + "\r\n"

VEHICLES = """\
{
    "Version": "2.5",
    "VehiclesParts": [
        {
            "VehicleName": "ZsmCartA",
            "Height": 0,
            "VehicleParts": [
                "ZsmCartWheel",
                "ZsmCartWheel",
                "ZsmCartAxle",
                "ZsmCartSeat"
            ]
        },
        {
            "VehicleName": "ZsmCartB",
            "Height": 0,
            "VehicleParts": [
                "ZsmCartWheel",
                "ZsmCartWheel",
                "ZsmCartAxle",
                "ZsmCartLamp",
                "ZsmCartLamp"
            ]
        }
    ]
}"""

INSURANCE = """\
{
    "Version": "2.5",
    "AuthorizedIDInsurance": [
        3
    ],
    "Insurances": [
        {
            "VehicleName": "ZsmCartA",
            "InsurancePriceCoefficient": 1.5,
            "CollateralMoneyCoefficient": 0.5
        },
        {
            "VehicleName": "ZsmCartB",
            "InsurancePriceCoefficient": 2.0,
            "CollateralMoneyCoefficient": 0.25
        },
        {
            "VehicleName": "ZsmRaft",
            "InsurancePriceCoefficient": 1.75,
            "CollateralMoneyCoefficient": 0.5
        }
    ]
}"""

GENERAL = """\
{
    "Version": "2.5",
    "ConvertTraderConfigToTraderPlus": 0,
    "ConvertTraderConfigToTraderPlusWithStockBasedOnCE": 0,
    "UseGarageToTradeCar": 0,
    "DisableHeightFailSafeForReceiptDeployment": 1,
    "HideInsuranceBtn": 1,
    "HideGarageBtn": 0,
    "HideLicenceBtn": 1,
    "EnableShowAllPrices": 1,
    "EnableShowAllCheckBox": 0,
    "IsReceiptTraderOnly": 0,
    "IsReceiptSaveLock": 1,
    "IsReceiptSaveAttachment": 0,
    "IsReceiptSaveCargo": 1,
    "LockPickChance": 0.40000000596046448,
    "LicenceKeyWord": "Permit",
    "Licences": [
        "Fixture Boat Permit",
        "Fixture Pilot Permit"
    ],
    "AcceptedStates": {
        "AcceptWorn": 1,
        "AcceptDamaged": 0,
        "AcceptBadlyDamaged": 0,
        "CoefficientWorn": 0.875,
        "CoefficientDamaged": 0.625,
        "CoefficientBadlyDamaged": 0.125
    },
    "StoreOnlyToPristineState": 1,
    "Currencies": [
        {
            "ClassName": "ZsmCoin_Gold",
            "Value": 400
        },
        {
            "ClassName": "ZsmCoin_Silver",
            "Value": 20
        }
    ],
    "Traders": [
        {
            "Id": 0,
            "Name": "ZsmTraderStall",
            "GivenName": "Fixture Ada",
            "Role": "Fixture Grocer",
            "Position": [
                9871.25,
                47.310001373291016,
                3204.60009765625
            ],
            "Orientation": [
                112.0,
                0.0,
                0.0
            ],
            "Clothes": []
        }
    ],
    "TraderObjects": [
        {
            "ObjectName": "ZsmFixtureCrate",
            "Position": [
                9880.0,
                47.0,
                3190.0
            ],
            "Orientation": [
                -71.0,
                0.0,
                0.0
            ]
        }
    ]
}"""

SAFEZONE = """\
{
    "Version": "2.5",
    "IsHideOutActive": 0,
    "EnableAfkDisconnect": 1,
    "KickAfterDelay": 45,
    "MsgEnterZone": "Fixture: zone entered",
    "MsgExitZone": "Fixture: zone left",
    "MsgOnLeavingZone": "Fixture: leaving in",
    "MustRemoveArmband": "Fixture: take the armband off first",
    "SafeAreaLocation": [
        {
            "SafeZoneStatut": "Fixture Market",
            "X": 9860.0,
            "Y": 3210.0,
            "Radius": 180.0,
            "Countdown": 20
        }
    ],
    "CleanUpTimer": 900,
    "WhitelistEntities": [],
    "ObjectsToDelete": [
        "ZsmLitter"
    ],
    "SZSteamUIDs": [
        "76561190000000011",
        "76561190000000022"
    ],
    "BlackListedItemInStash": [
        "ZsmPebble",
        "ZsmTwig",
        "ZsmSpoon"
    ]
}"""

GARAGE = """\
{
    "Version": "2.5",
    "UseGarageOnlyToTrade": 0,
    "VehicleMustHaveLock": 0,
    "SaveVehicleLock": 0,
    "SaveVehicleCargo": 1,
    "SaveVehicleHealth": 0,
    "SaveVehicleFuel": 1,
    "SavedVehicleInGarageForTradeInHour": 3,
    "MaxVehicleStored": 2,
    "ParkInCost": 40,
    "ParkOutCost": 60,
    "PayWithBankAccount": 1,
    "WhitelistedObjects": [
        "ZsmFixturePad"
    ],
    "NPCs": [
        {
            "ClassName": "ZsmFixtureKeeper",
            "Position": [
                5512.7998046875,
                73.199996948242188,
                5519.2998046875
            ],
            "Orientation": [
                95.0,
                -0.0,
                -0.0
            ],
            "ParkingPosition": [
                5520.25,
                74.050003051757812,
                5530.5
            ],
            "ParkingOrientation": [
                95.0,
                0.0,
                0.0
            ],
            "Clothes": [
                "ZsmApron",
                "ZsmBoots",
                "ZsmCap"
            ]
        }
    ],
    "ParkingNotAvailable": "Fixture: spot taken",
    "NotEnoughMoney": "Fixture: short of money",
    "NotRightToPark": "Fixture: not yours to park",
    "CarHasMember": "Fixture: someone is inside",
    "ParkInFail": "Fixture: park in failed",
    "ParkInSuccess": "Fixture: parked",
    "ParkOutFail": "Fixture: park out failed",
    "ParkOutSuccess": "Fixture: out of the garage",
    "MaxVehicleStoredReached": "Fixture: garage full",
    "TradeVehicleWarning": "Fixture: stored %1 hours",
    "TradeVehicleHasBeenDeleted": "Fixture: stored too long, removed"
}"""

PRICE = """\
{
    "Version": "2.5",
    "EnableAutoCalculation": 0,
    "EnableAutoDestockAtRestart": 0,
    "EnableDefaultTraderStock": 0,
    "TraderCategories": [
        {
            "CategoryName": "Fixture Roots",
            "Products": [
                "ZsmTurnip,1,-1,30,1,-1",
                "ZsmParsnip,1,-1,40,1,-1",
                "ZsmRadish,1,-1,65,1,-1"
            ]
        },
        {
            "CategoryName": "Fixture Tins",
            "Products": [
                "ZsmTinBeans,1,-1,150,1,-1",
                "ZsmTinFish,1,-1,320,2,1",
                "ZsmTinSoup,1,-1,410,3,1"
            ]
        }
    ]
}"""

BEANS = """\
{
    "Info": "Fixture: what the badge does.",
    "PartnerCode": "FIXTURE1",
    "HideBadge": 0
}"""

TP_FILES = {
    "TraderPlusBankingConfig.json": BANKING,
    "TraderPlusIDsConfig.json": IDS,
    "TraderPlusVehiclesConfig.json": VEHICLES,
    "TraderPlusInsuranceConfig.json": INSURANCE,
    "TraderPlusGeneralConfig.json": GENERAL,
    "TraderPlusSafeZoneConfig.json": SAFEZONE,
    "TraderPlusGarageConfig.json": GARAGE,
    "TraderPlusPriceConfig.json": PRICE,
    "TraderPlusBeansLauncherConfig.json": BEANS,
}


def tp_dir(cfg) -> Path:
    return cfg.server_dir_path / cfg.profiles_path / "TraderPlus" / "TraderPlusConfig"


@pytest.fixture()
def tp(sample_config):
    """TraderPlus' profile folder in the layout the mod's own files have."""
    folder = tp_dir(sample_config)
    folder.mkdir(parents=True)
    for name, text in TP_FILES.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    return sample_config


def _expansion_doc(entry) -> dict:
    """One settings file in the shapes Expansion's files hold: flags as ints,
    floats, text, lists of names and of numbers, a mixed list, a list of
    objects with positions, a map, and the lists a stock file leaves empty."""
    doc = {
        "m_Version": 12, "Enabled": 1, "Chance": 0.25, "Label": "Expansion",
        "Names": ["One", "Two"], "Numbers": [1.5, 2.5], "Mixed": ["a", 1],
        "Entries": [
            {"Name": "A", "Position": [1500.5, 20.0, 3200.25], "Radius": 50.0},
            {"Name": "B", "Position": [4000.0, 5.0, 6000.0], "Radius": 10.0},
        ],
        "ByLevel": {"0": 100.0, "1": 10.0},
    }
    for key in exp.EMPTY_OBJECT_LISTS.get(entry.filename, {}):
        doc[key] = []
    return doc


@pytest.fixture()
def expf(sample_config, tmp_mission_dir):
    """All 30 of Expansion's settings files."""
    for entry in exp.SETTINGS:
        path = exp.settings_path(sample_config, entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_expansion_doc(entry)), encoding="utf-8")
    return sample_config


MOD_GROUPS = {"TraderPlus", "RadZone", "ToxicZone", "Dynamic AI", "Expansion"}
MOD_EDITORS = sorted(s.id for s in editors.list_editors() if s.group in MOD_GROUPS)
VARIANTS = ("enforce", "unknown_keys", "crlf_bom")


def _fixture_for(editor_id: str) -> str:
    if editor_id.startswith("tp-"):
        return "tp"
    if editor_id.startswith("radzone-"):
        return "rz"
    if editor_id.startswith("toxiczone-"):
        return "tzc"
    if editor_id.startswith("dynai-"):
        return "dyn"
    if editor_id == "expansion-ai-patrol-list":
        return "eai"
    return "expf"


def test_every_mod_editor_is_swept():
    # A new mod's editors join MOD_GROUPS, and with it every test below.
    groups = {s.group for s in editors.list_editors()} - {"Mission", "Server"}
    assert groups == MOD_GROUPS
    assert len(MOD_EDITORS) == 58


# ── The file as the mod writes it ───────────────────────────────────────


def _enforce_float(value: float) -> SourceFloat:
    """A float as Enforce's JsonFileLoader writes it: the float32 the game
    holds, printed with 17 significant digits, a whole number with ``.0``."""
    text = format(struct.unpack("<f", struct.pack("<f", value))[0], ".17g")
    if not re.search(r"[.eE]", text):
        text += ".0"
    return SourceFloat(text)


def _enforced(value):
    if isinstance(value, dict):
        return {k: _enforced(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_enforced(v) for v in value]
    if isinstance(value, float):
        return _enforce_float(value)
    return value


def enforce_text(doc) -> str:
    """*doc* laid out the way the mods' files are: 4 spaces, every list item
    on its own line, LF, no final newline (the TraderPlus files above)."""
    return dumps_json(_enforced(doc))


def _variant(text: str, variant: str) -> str:
    if variant == "unknown_keys":
        # A key no editor knows, in every object: a later mod release's, or
        # the admin's own.
        return re.sub(
            r"\{(\r?\n)([ \t]*)",
            lambda m: "{" + m[1] + m[2] + '"ZsmUnknownKey": 7,' + m[1] + m[2],
            text,
        )
    if variant == "crlf_bom":
        return "﻿" + text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


def _audio(name: str) -> dict:
    entry = dai._location(name)
    entry["Spatial_Sensitivity"] = 3.0
    return entry


def _slots(key: str) -> dict:
    return {key: [
        {"SlotName": "Mask", "ListProtections": [{"ClotheName": "GP5GasMask", "Protection": 1}]},
        {"SlotName": "Gloves", "ListProtections": [{"ClotheName": "NBCGlovesGray", "Protection": 1}]},
    ]}


def _two_of_everything(name: str, cfg) -> None:
    """Every table of the mod with two rows, so a row can be removed and
    copied, and every list the mod's editors show with entries in it."""
    if name == "dyn":
        dai._write(cfg, dai._settings(
            Group=[dai._group("Guard"), dai._group("Patrol", faction="West")],
            Point=[
                dai._point("Airfield", Spatial_Position=[4500.0, 12.5, 10200.0], Spatial_Radius=150.0),
                dai._point("Camp", Spatial_Safe=1, Spatial_Position=[3000.0, 5.0, 3000.0]),
            ],
            Location=[dai._location("Tower"), dai._location("Bridge")],
            Audio=[_audio("Shots"), _audio("Engine")],
        ))
        dai._write(cfg, dai._notes("Guard", "Patrol"), name="Notificationsettings.json")
    elif name == "rz":
        rzt._write(cfg, zone_mods.RADIATION, rzt._radiation(
            rzt._zone("North"), rzt._zone("South", x=7000.0, y=2000.0, radius=150.5)))
        rzt._write(cfg, zone_mods.CRATES, rzt._crates(
            rzt._area("North"), rzt._area("South", (7000.0, 50.0, 2000.0))))
        rzt._write(cfg, zone_mods.CLOTHES, _slots("RZListSlotProtection"))
    elif name == "tzc":
        tzt._write(cfg, toxic_zone.STATIC, tzt._static(
            tzt._static_zone("Plant"), tzt._static_zone("Mine", x=8000.0, y=9000.0)))
        tzt._write(cfg, toxic_zone.MOVING, tzt._moving(
            tzt._moving_zone("Line"), tzt._moving_zone("Arc", start=(500.0, 800.0), end=(2500.0, 900.0))))
        tzt._write(cfg, toxic_zone.CLOTHES, {"RequiredProtection": 5, **_slots("TZListSlotProtection")})


def _targets(spec, cfg) -> list[Path]:
    return [t.path for t in spec.raw_targets(cfg) if t.path.is_file()]


def _prepare(request, editor_id: str, variant: str):
    """The editor's install, its files laid out as the mod writes them."""
    name = _fixture_for(editor_id)
    cfg = request.getfixturevalue(name)
    _two_of_everything(name, cfg)
    spec = editors.get_editor(editor_id)
    paths = _targets(spec, cfg)
    assert paths, editor_id
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        if name != "tp":
            text = enforce_text(json.loads(text))
        path.write_bytes(_variant(text, variant).encode("utf-8"))
    return cfg, spec


def _files(cfg) -> dict[Path, bytes]:
    """Every config file under the server directory: the mods' and the
    mission's, not zsm's own (its Versions, the faction cache)."""
    own = paths.CONFIG_DIR
    return {
        p: p.read_bytes()
        for p in cfg.server_dir_path.rglob("*.json")
        if own not in p.parents and not p.name.startswith(".")
    }


def _restore(files: dict[Path, bytes]) -> None:
    for path, data in files.items():
        path.write_bytes(data)


def _save(spec, cfg, payload) -> None:
    writes = spec.save(cfg, payload)
    if callable(writes):
        writes()


def _changed(before: dict[Path, bytes], cfg) -> dict[Path, bytes]:
    after = _files(cfg)
    return {p: after[p] for p in after if before.get(p) != after[p]}


# ── What the page sends ─────────────────────────────────────────────────


def _fields(body: dict) -> list[dict]:
    return [f for s in body["sections"] for f in s["fields"] if not f.get("readonly")]


def _with_nested(values: dict, nested: dict | None) -> dict:
    if nested:
        values[NESTED] = copy.deepcopy(nested)
        values[NESTED_BASE] = copy.deepcopy(nested)
    return values


def _every_value(body: dict):
    """The whole form or table sent back as loaded."""
    if body["kind"] == "form":
        return _with_nested({f["key"]: copy.deepcopy(f["value"]) for f in _fields(body)}, body.get("nested"))
    return [
        {"id": r["id"], "values": _with_nested(copy.deepcopy(r["values"]), r.get("nested"))}
        for r in body["rows"]
    ]


# ── Comparing a save with the file it replaced ──────────────────────────


def _doc(data: bytes):
    return json.loads(data.decode("utf-8-sig"))


def _leaf_changes(old, new, path=()) -> list[tuple]:
    """(path, old, new) for every value that differs; a list whose length
    changed, or a value whose type changed, is one change."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in dict.fromkeys([*old, *new]):
            if key not in old or key not in new:
                out.append((path + (key,), old.get(key), new.get(key)))
            else:
                out += _leaf_changes(old[key], new[key], path + (key,))
        if list(old) != [k for k in new if k in old]:
            out.append((path, "key order", "key order"))
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            out += _leaf_changes(a, b, path + (i,))
        return out
    if type(old) is type(new) and old == new:
        return []
    return [(path, old, new)]


def _line_changes(old: bytes, new: bytes) -> tuple[int, int]:
    """(lines removed, lines added), each line with its line ending."""
    a = old.decode("utf-8").splitlines(keepends=True)
    b = new.decode("utf-8").splitlines(keepends=True)
    removed = added = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            removed += i2 - i1
            added += j2 - j1
    return removed, added


#: Values a save derives from the edited ones, so they change with them:
#: a moving toxic zone's path from its two ends (``toxic_zone.apply_derived``).
DERIVED = {"toxiczone-moving-zones": {"Coeff", "YtoOrigin", "Norme"}}


def _one_change(before: dict[Path, bytes], cfg, derived=frozenset()) -> tuple[Path, tuple, int]:
    """The one file a save changed, its one changed value, and how many
    derived values changed with it."""
    changed = _changed(before, cfg)
    assert len(changed) == 1, [p.name for p in changed]
    (path, data), = changed.items()
    changes = _leaf_changes(_doc(before[path]), _doc(data))
    edited = [c for c in changes if c[0][-1] not in derived]
    assert len(edited) == 1, changes
    return path, edited[0], len(changes) - 1


# ── 1. No edits, no change ──────────────────────────────────────────────


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("editor_id", MOD_EDITORS)
def test_a_save_with_no_edits_leaves_every_file_byte_identical(request, editor_id, variant, server_stopped):
    cfg, spec = _prepare(request, editor_id, variant)
    before = _files(cfg)
    body = spec.load(cfg)
    _save(spec, cfg, _every_value(body))
    assert _changed(before, cfg) == {}
    # A form the page saves with nothing changed sends nothing.
    if body["kind"] == "form":
        _save(spec, cfg, {})
        assert _changed(before, cfg) == {}
    # And it loads the same afterwards: the round trip lost nothing.
    assert spec.load(cfg) == body


# ── 2. One edit, one change ─────────────────────────────────────────────


# A table whose rows are keyed by a value the admin types refuses two rows
# with one key (fed369b, ccea279), so the edit and the copy below pick a free
# key there.
UNIQUE_KEYS = {"tp-ids": "id", "tp-vehicles": "vehicle_name"}


def _free_key(field: dict, rows: list[dict], key: str):
    """A value for the unique *key* no row holds, and what the file holds then."""
    used = {str(r["values"].get(key)) for r in rows}
    if field["type"] == "int":
        n = max(int(v) for v in used if v.lstrip("-").isdigit()) + 100
        return (str(n), n)
    name = "ZsmFreeKey"
    return (name, name)


def _edited(field: dict, value):
    """Another value of the field's kind, and what the file should then hold
    (None: skip, there is no other value to pick)."""
    choices = field.get("choices")
    if choices:
        other = [c for c in choices if str(c) != str(value)]
        return (other[0], other[0]) if other else None
    kind = field["type"]
    if value in ("", None) and kind in ("int", "float"):
        value = 0  # a key this entry does not have: the edit adds it
    if kind == "bool":
        return (not value, int(not value))
    if kind == "int":
        return (str(int(value) + 1), int(value) + 1)
    if kind == "float":
        return (str(float(value) + 0.5), float(value) + 0.5)
    if kind == "str":
        if str(value).startswith("["):  # a mixed list, edited as JSON text
            items = json.loads(value)
            return (json.dumps(items[:-1]), items[:-1]) if items else None
        return (f"{value} edited", f"{value} edited")
    return None


def _same_value(want, got) -> bool:
    if isinstance(want, float) or isinstance(got, float):
        return isinstance(got, (int, float)) and math.isclose(float(want), float(got), rel_tol=1e-9)
    if isinstance(got, bool) or isinstance(want, bool):
        return int(want) == int(got)
    return str(want) == str(got)


def _list_edits(field: dict, value: list):
    """An item added at the end, and the first item removed."""
    item = {"str": "ZsmAdded", "int": 7, "float": 7.5}[field.get("item", "str")]
    yield value + [item]
    if value:
        yield value[1:]


def _scalar_cells(body: dict):
    """(key, field) for every value the page edits as one input."""
    if body["kind"] == "form":
        return [(f["key"], f) for f in _fields(body)]
    return [(c["key"], c) for c in body["columns"] if not c.get("readonly")]


def _payload(body: dict, key: str, raw):
    """What the page sends after one input changed: a form only that key,
    a table every row with the first row's cell changed."""
    if body["kind"] == "form":
        return {key: raw}
    rows = _every_value(body)
    rows[0]["values"][key] = raw
    return rows


@pytest.mark.parametrize("variant", ("unknown_keys", "crlf_bom"))
@pytest.mark.parametrize("editor_id", MOD_EDITORS)
def test_an_edit_changes_only_the_edited_value(request, editor_id, variant, server_stopped):
    cfg, spec = _prepare(request, editor_id, variant)
    before = _files(cfg)
    body = spec.load(cfg)
    if body["kind"] == "table" and not body["rows"]:
        pytest.skip("no rows in the fixture")
    tried = 0
    for key, field in _scalar_cells(body):
        value = (field.get("value") if body["kind"] == "form" else body["rows"][0]["values"].get(key))
        if field["type"] == "list":
            edits = [(raw, raw) for raw in _list_edits(field, value)]
        elif UNIQUE_KEYS.get(editor_id) == key:
            edits = [_free_key(field, body["rows"], key)]
        else:
            edit = _edited(field, value)
            edits = [edit] if edit else []
        for raw, want in edits:
            _save(spec, cfg, _payload(body, key, raw))
            path, (where, old, got), with_it = _one_change(before, cfg, DERIVED.get(editor_id, ()))
            if isinstance(want, list):
                assert got == want, (key, where)
            else:
                assert _same_value(want, got), (key, where, want, got)
            removed, added = _line_changes(before[path], path.read_bytes())
            # A changed line is one out, one in; a list item or a key the
            # entry did not have yet adds a line and a comma.
            limit = 2 if not isinstance(want, list) else 4
            limit += (old is None) + 2 * with_it
            assert removed + added <= limit, (key, removed, added)
            if variant == "crlf_bom":
                text = path.read_bytes()
                assert text.startswith("﻿".encode()) and b"\n" not in text.replace(b"\r\n", b"")
            _restore(before)
            tried += 1
    assert tried, "no field was edited"


def _first_number(value, path=()):
    """The path of the first number in *value*, depth first."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return path
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, item in items:
        found = _first_number(item, path + (key,))
        if found is not None:
            return found
    return None


def _bumped(value, path):
    out = copy.deepcopy(value)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = target[path[-1]] + 1
    return out


@pytest.mark.parametrize("editor_id", MOD_EDITORS)
def test_a_nested_edit_changes_only_that_value(request, editor_id, server_stopped):
    cfg, spec = _prepare(request, editor_id, "unknown_keys")
    before = _files(cfg)
    body = spec.load(cfg)
    if body["kind"] == "form":
        nested = body.get("nested") or {}
        where = _first_number(nested)
        if where is None:
            pytest.skip("nothing nested with a number")
        payload = _every_value(body)
        payload[NESTED] = _bumped(nested, where)
    else:
        rows = [r for r in body["rows"] if _first_number(r.get("nested") or {}) is not None]
        if not rows:
            pytest.skip("no row with a nested number")
        payload = _every_value(body)
        row = next(p for p in payload if p["id"] == rows[0]["id"])
        row["values"][NESTED] = _bumped(rows[0]["nested"], _first_number(rows[0]["nested"]))
    _save(spec, cfg, payload)
    path, (_where, old, new), _ = _one_change(before, cfg)
    assert new == old + 1
    assert sum(_line_changes(before[path], path.read_bytes())) == 2


# ── 3. Rows added and removed ───────────────────────────────────────────

TABLES = [e for e in MOD_EDITORS if editors.get_editor(e).kind == "table"]


@pytest.mark.parametrize("editor_id", TABLES)
def test_a_removed_row_takes_only_its_own_lines(request, editor_id, server_stopped):
    cfg, spec = _prepare(request, editor_id, "unknown_keys")
    before = _files(cfg)
    body = spec.load(cfg)
    if len(body["rows"]) < 2:
        pytest.skip("fewer than two rows in the fixture")
    _save(spec, cfg, _every_value(body)[:-1])
    changed = _changed(before, cfg)
    assert len(changed) == 1
    (path, data), = changed.items()
    old, new = _doc(before[path]), _doc(data)
    (where, was, now), = _leaf_changes(old, new)
    assert now == was[:-1]
    removed, added = _line_changes(before[path], data)
    assert added <= 1  # the comma the new last entry no longer needs


@pytest.mark.parametrize("editor_id", TABLES)
def test_a_copied_row_is_its_source_and_leaves_the_rest_alone(request, editor_id, server_stopped):
    cfg, spec = _prepare(request, editor_id, "unknown_keys")
    before = _files(cfg)
    body = spec.load(cfg)
    if not body["rows"]:
        pytest.skip("no rows in the fixture")
    rows = _every_value(body)
    copy_of = copy.deepcopy(rows[0])
    free = None
    if editor_id in UNIQUE_KEYS:
        key = UNIQUE_KEYS[editor_id]
        field = next(c for c in body["columns"] if c["key"] == key)
        free = _free_key(field, body["rows"], key)[1]
        copy_of["values"][key] = free
    if body.get("duplicate"):
        copy_of["id"] = f"dup-{rows[0]['id']}-1"
    else:
        copy_of["id"] = "new-1"
    rows.append(copy_of)
    _save(spec, cfg, rows)
    changed = _changed(before, cfg)
    (path, data), = changed.items()
    old, new = _doc(before[path]), _doc(data)
    (where, was, now), = _leaf_changes(old, new)
    assert now[:-1] == was
    if free is not None:  # the copy's key is the free one; the rest is the source's
        source_key = next(k for k in was[0] if was[0][k] == rows[0]["values"][UNIQUE_KEYS[editor_id]])
        assert now[-1][source_key] == free
        now[-1][source_key] = was[0][source_key]
    if body.get("duplicate"):
        assert now[-1] == was[0]  # every key of the source, unknown ones included
    else:
        # A new row starts blank: what it has is what the row sent.
        assert now[-1] == {k: v for k, v in was[0].items() if k != "ZsmUnknownKey"}
    removed, added = _line_changes(before[path], data)
    assert removed <= 1  # the comma the old last entry now needs


# ── 4. The map editor's TraderPlus layers ───────────────────────────────


@pytest.fixture()
def tp_map(tp, tmp_mission_dir):
    from tests.test_spawn_editor_api import _client

    return _client(tp)


def _tp_bytes(cfg, name: str) -> bytes:
    return (tp_dir(cfg) / name).read_bytes()


def _changed_lines(before: bytes, after: bytes) -> list[tuple[str, str]]:
    old, new = before.decode().splitlines(), after.decode().splitlines()
    assert len(old) == len(new)
    return [(a.strip(), b.strip()) for a, b in zip(old, new) if a != b]


def test_map_popups_saved_unchanged_leave_the_files_byte_identical(tp, tp_map):
    before = {name: _tp_bytes(tp, name) for name in TP_FILES}
    [trader, *_] = tp_map.get("/api/map-editor/traders").json()["traders"]
    fields = ("game_x", "game_y", "game_z", "orientation", "given_name", "role", "name", "id")
    assert tp_map.put("/api/map-editor/traders/0", json={k: trader[k] for k in fields}).status_code == 200
    [zone] = tp_map.get("/api/map-editor/safezones").json()["safezones"]
    fields = ("name", "game_x", "game_z", "radius", "countdown")
    assert tp_map.put("/api/map-editor/safezones/0", json={k: zone[k] for k in fields}).status_code == 200
    [npc] = tp_map.get("/api/map-editor/garage").json()["garage_npcs"]
    fields = ("class_name", "game_x", "game_y", "game_z", "park_game_x", "park_game_y",
              "park_game_z", "orientation", "park_orientation")
    assert tp_map.put("/api/map-editor/garage/0", json={k: npc[k] for k in fields}).status_code == 200
    assert {name: _tp_bytes(tp, name) for name in TP_FILES} == before


def test_a_trader_drag_changes_only_its_x_and_z(tp, tp_map):
    before = _tp_bytes(tp, "TraderPlusGeneralConfig.json")
    [trader, *_] = tp_map.get("/api/map-editor/traders").json()["traders"]
    resp = tp_map.put("/api/map-editor/traders/0", json={"lat": trader["lat"] + 1, "lng": trader["lng"] + 1})
    assert resp.status_code == 200, resp.text
    changed = _changed_lines(before, _tp_bytes(tp, "TraderPlusGeneralConfig.json"))
    assert [old for old, _ in changed] == ["9871.25,", "3204.60009765625"]


def test_a_safe_zone_keeps_a_fractional_radius(tp, tp_map):
    """SafeZone declares ``float X, Y, Radius;`` (SafeZoneLocation.c in the
    mod's PBO); the layer read it as an int and wrote 150.5 back as 150."""
    before = _tp_bytes(tp, "TraderPlusSafeZoneConfig.json")
    assert tp_map.put("/api/map-editor/safezones/0", json={"radius": 150.5}).status_code == 200
    after = _tp_bytes(tp, "TraderPlusSafeZoneConfig.json")
    assert _changed_lines(before, after) == [('"Radius": 180.0,', '"Radius": 150.5,')]
    [zone] = tp_map.get("/api/map-editor/safezones").json()["safezones"]
    assert zone["radius"] == 150.5


def test_a_safe_zone_refuses_a_fractional_countdown(tp, tp_map):
    """SafeZone declares ``int Countdown;``: 4.5 was cut to 4 without a word."""
    before = _tp_bytes(tp, "TraderPlusSafeZoneConfig.json")
    resp = tp_map.put("/api/map-editor/safezones/0", json={"countdown": 4.5, "radius": 120})
    assert resp.status_code == 400 and "whole number" in resp.json()["detail"]
    assert _tp_bytes(tp, "TraderPlusSafeZoneConfig.json") == before


# ── 5. What the mods' own code says a value may be ──────────────────────
#
# Each type below is the declaration in the mod's script, read from the PBOs of
# the Workshop build in the test container (2026 Q4): GarageSettings.c,
# SafeZoneLocation.c / SafeAreaSettings.c, TraderPlusBankingSettings.c,
# TraderPlusGeneralSettings.c. A value the type cannot hold is refused; it used
# to be cut to the whole number below it (``int(4.5)``) or read as true
# (``bool("false")``).


def _tp_save(cfg, editor_id: str, payload) -> None:
    _save(editors.get_editor(editor_id), cfg, payload)


@pytest.mark.parametrize(("editor_id", "key", "value"), [
    ("tp-garage", "max_vehicle_stored", "4.5"),       # int MaxVehicleStored
    ("tp-garage", "park_in_cost", "99.9"),            # int ParkInCost
    ("tp-banking", "default_max_currency", "1e3.5"),  # int DefaultMaxCurrency
    ("tp-safezone", "area:0:countdown", "4.5"),       # int Countdown
    ("tp-safezone", "kick_after_delay", "abc"),       # int KickAfterDelay
    ("tp-general", "lock_pick_chance", "often"),      # float LockPickChance
])
def test_a_value_the_mod_s_type_cannot_hold_is_refused(tp, editor_id, key, value):
    before = {name: _tp_bytes(tp, name) for name in TP_FILES}
    with pytest.raises(ValueError):
        _tp_save(tp, editor_id, {key: value})
    assert {name: _tp_bytes(tp, name) for name in TP_FILES} == before


def test_a_safe_area_radius_takes_a_fraction(tp):
    """``float X, Y, Radius;``: the editor took Radius as an int (refusing
    150.5) and the reader cut the file's value to a whole number."""
    before = _tp_bytes(tp, "TraderPlusSafeZoneConfig.json")
    _tp_save(tp, "tp-safezone", {"area:0:radius": "150.5"})
    after = _tp_bytes(tp, "TraderPlusSafeZoneConfig.json")
    assert _changed_lines(before, after) == [('"Radius": 180.0,', '"Radius": 150.5,')]


@pytest.mark.parametrize(("editor_id", "key", "line"), [
    ("tp-banking", "banking_logs", '"BankingLogs": 1,'),
    ("tp-garage", "use_garage_only_to_trade", '"UseGarageOnlyToTrade": 1,'),
    ("tp-safezone", "is_hideout_active", '"IsHideOutActive": 1,'),
])
def test_a_ticked_box_is_written_as_1_like_the_mod_writes_it(tp, editor_id, key, line):
    """Enforce's JsonFileLoader writes a bool as 0/1, and the files hold 0/1."""
    name = {"tp-banking": "TraderPlusBankingConfig.json", "tp-garage": "TraderPlusGarageConfig.json",
            "tp-safezone": "TraderPlusSafeZoneConfig.json"}[editor_id]
    before = _tp_bytes(tp, name)
    _tp_save(tp, editor_id, {key: True})
    [(_old, new)] = _changed_lines(before, _tp_bytes(tp, name))
    assert new == line
    _tp_save(tp, editor_id, {key: "false"})
    assert _tp_bytes(tp, name) == before


def test_a_trader_entry_keeps_the_keys_no_editor_knows(tp):
    """A later TraderPlus release may add a key to an entry; a save of the
    IDs table used to rebuild every entry from the keys zsm knows."""
    path = tp_dir(tp) / "TraderPlusIDsConfig.json"
    text = path.read_bytes().decode()
    path.write_bytes(text.replace('"Id": 0,', '"Id": 0,\n            "FutureKey": [1, 2],', 1).encode())
    before = path.read_bytes()
    spec = editors.get_editor("tp-ids")
    rows = _every_value(spec.load(tp))
    rows[0]["values"]["id"] = 5
    _save(spec, tp, rows)
    assert _changed_lines(before, path.read_bytes()) == [('"Id": 0,', '"Id": 5,')]


def test_a_name_emptied_in_its_box_is_dropped_not_written_as_an_empty_string(tp):
    """Clearing a list cell's only item sent ``[""]``, and the file got
    ``"LicencesRequired": [ "" ]`` where it had ``[]`` (found in the browser)."""
    path = tp_dir(tp) / "TraderPlusIDsConfig.json"
    spec = editors.get_editor("tp-ids")
    rows = _every_value(spec.load(tp))
    before = path.read_bytes()
    rows[0]["values"]["licences_required"] = [""]
    _save(spec, tp, rows)
    assert path.read_bytes() == before


def test_a_price_save_keeps_every_untouched_line_and_unknown_key(tp):
    """The price file: one product changed is one line changed, and a key no
    editor knows (top level or in a category) stays."""
    from app.traderplus import traderplus_json as tpj

    path = tp_dir(tp) / "TraderPlusPriceConfig.json"
    text = path.read_bytes().decode()
    text = text.replace('"Version": "2.5",', '"Version": "2.5",\n    "FutureKey": 1,')
    text = text.replace('"CategoryName": "Fixture Roots",', '"CategoryName": "Fixture Roots",\n            "Icon": "roots",')
    path.write_bytes(text.encode())
    before = path.read_bytes()
    cfg = tpj.parse_price_config(path)
    tpj.save_price_config(cfg)
    assert path.read_bytes() == before
    cfg.categories[0].products[1].buy_price = 60
    tpj.save_price_config(cfg)
    assert _changed_lines(before, path.read_bytes()) == [
        ('"ZsmParsnip,1,-1,40,1,-1",', '"ZsmParsnip,1,-1,40,60,-1",')]


def test_a_row_added_to_the_ids_file_takes_its_lf_lines(tp):
    """TraderPlusIDsConfig.json ships with LF lines and one CRLF at the very
    end; an entry added to it took CRLF from that one ending."""
    path = tp_dir(tp) / "TraderPlusIDsConfig.json"
    spec = editors.get_editor("tp-ids")
    rows = _every_value(spec.load(tp))
    rows.append({"id": "new-1", "values": {**rows[0]["values"], "id": 9}})
    _save(spec, tp, rows)
    data = path.read_bytes()
    assert data.count(b"\r\n") == 1 and data.endswith(b"}\r\n")


def test_a_fractional_safe_area_radius_survives_a_save_with_no_edits(tp):
    """The reader cut ``Radius`` to a whole number, so the form showed 150
    and saved it over the file's 150.5."""
    path = tp_dir(tp) / "TraderPlusSafeZoneConfig.json"
    path.write_bytes(path.read_bytes().replace(b'"Radius": 180.0,', b'"Radius": 150.5,'))
    before = path.read_bytes()
    spec = editors.get_editor("tp-safezone")
    _save(spec, tp, _every_value(spec.load(tp)))
    assert path.read_bytes() == before


def test_a_price_line_written_another_way_stays_as_written(tp):
    """A line the admin wrote by hand (spaces, ``1.0``) says the same product
    as zsm's own format; it is kept as written while it does."""
    from app.traderplus import traderplus_json as tpj

    path = tp_dir(tp) / "TraderPlusPriceConfig.json"
    path.write_bytes(path.read_bytes().replace(b'"ZsmRadish,1,-1,65,1,-1"', b'"ZsmRadish, 1.0, -1, 65, 1, -1"'))
    before = path.read_bytes()
    cfg = tpj.parse_price_config(path)
    cfg.categories[1].products[0].sell_price = 7
    tpj.save_price_config(cfg)
    assert _changed_lines(before, path.read_bytes()) == [
        ('"ZsmTinBeans,1,-1,150,1,-1",', '"ZsmTinBeans,1,-1,150,1,7",')]
