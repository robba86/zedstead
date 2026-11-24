"""ToxicZone editors (plans/48, ToxicZone pass B): its five TZConfig files as
forms and tables.

Built on ``_mod_json.py``, like RadZone's, so a save patches and whole numbers
stay whole. A zone's particles, crate positions and creatures, and a slot's
clothes, are edited as a tree under the row (``Table.tree``, plans/76 step 2b). The field help is written here in zsm's own words from what the mod's
source and explanation files say; nothing of the mod's is copied, and nothing
is ever written inside ``@ToxicZone/``. A key whose meaning the mod's notes
contradict (``IsOnlyGasMask``) has no help rather than a guess.

**Moving zones** are the part with rules of their own (D3, D7, D8): the table
shows the two ends, and every save writes ``Coeff``, ``YtoOrigin`` and
``Norme`` from them through ``toxic_zone.apply_derived``, which also gives
``Speed`` the sign of the direction. The table shows ``Speed`` positive and
hides the three derived values. A north-south path is refused.

**AdminUID** (D4) is on the moving-zone settings form as a list, and
``replace_author_uid`` is the confirmed one-click replace the report card
offers while the list holds the author's own ID.

``recompute_paths`` is the card's "Fix the paths": the table's Save only
enables after an edit, so a zone whose file values are already wrong (the mod's
own default is) needs a way to be recomputed without a pretend edit.
"""

from __future__ import annotations

from pathlib import Path

from app.ops import toxic_zone as ops
from app.ops.mod_files import json_number
from app.ops.priority_ops import is_valid_steam64
from app.ops.zone_mods import decimals_in_whole_numbers
from app.shared.config import DayzConfig
from app.shared.json_patch import save_json_patched
from app.web.editors import EditorSpec, register
from app.web.editors._mod_json import (
    TREE_REST,
    F,
    Form,
    Table,
    document,
    form_load,
    form_save,
    table_load,
    table_save,
    whole_number_validator,
)
from app.web.editors._raw import raw_targets_from

GROUP = "ToxicZone"
MOD = "ToxicZone"


def _path(name: str):
    def path(config: DayzConfig) -> Path:
        return ops.config_dir(config) / name

    return path


_general_path = _path(ops.GENERAL)
_clothes_path = _path(ops.CLOTHES)
_loot_path = _path(ops.LOOT)
_static_path = _path(ops.STATIC)
_moving_path = _path(ops.MOVING)

_MESSAGES = (
    F("IsMsgActive", "bool", "Tell players when they enter and leave the zone."),
    F("MsgEnterZone", "str"),
    F("MsgExitZone", "str"),
)


# ── General ─────────────────────────────────────────────────────────────

GENERAL_FORM = Form(
    mod=MOD,
    path=_general_path,
    sections=(
        ("Sickness", (
            F("NbSickGiven", "int",
              "The most toxicity an unprotected player takes on per minute in a static zone. "
              "A player gets sick at 100; each protective piece worn cuts this by its share "
              "of RequiredProtection."),
            F("ToxicBloodLoss", "int",
              "Blood a sick player loses every few seconds until cured."),
            F("SuitsDamage", "int",
              "Damage a protective suit takes each time its wearer enters a zone."),
        )),
        ("Toxic grenades", (
            F("NbSickGivenforToxicSmoke", "int",
              "Toxicity a player without a gas mask takes on per minute in a toxic grenade's "
              "smoke."),
            F("GP5DamageGrenade", "int",
              "Damage a gas mask takes every 15 seconds in a toxic grenade's smoke."),
        )),
    ),
)


# ── Clothes protection ──────────────────────────────────────────────────

PROTECTION_FORM = Form(
    mod=MOD,
    path=_clothes_path,
    not_on_form=frozenset({"TZListSlotProtection"}),
    sections=(
        ("Protection", (
            F("RequiredProtection", "int",
              "Protection points a player needs to be fully protected. The default wants six: "
              "the five NBC pieces give one each, and the GP5 gas mask gives its point only "
              "with a filter in."),
        )),
    ),
)

CLOTHES = Table(
    id="toxiczone-clothes",
    title="ToxicZone Clothes Protection",
    mod=MOD,
    path=_clothes_path,
    section="TZListSlotProtection",
    name_key="SlotName",
    add_row=False,
    # The clothes that protect in this slot and how many points each gives. The
    # mod checks them in order, so a vanilla item goes after a modded one.
    tree=frozenset({"ListProtections"}),
    warnings=(
        "Open a slot to edit its clothes and the points each gives. The mod checks them in "
        "order, so keep a vanilla item after a modded one when both are listed.",
    ),
    columns=(F("SlotName", "str", "The clothing slot."),),
)


# ── Loot ────────────────────────────────────────────────────────────────

LOOT_FORM = Form(
    mod=MOD,
    path=_loot_path,
    not_on_form=frozenset({"ListLoots"}),
    tree=frozenset({"ListLoots"}),
    sections=(
        ("Crates", (
            F("IsActive", "bool", "Off: no loot crates spawn in any zone."),
            F("TimerLoot", "int",
              "Milliseconds between crate spawns; the old crate is removed just before. "
              "2700000 is 45 minutes."),
        )),
    ),
)


# ── Static zones ────────────────────────────────────────────────────────

STATIC_FORM = Form(
    mod=MOD,
    path=_static_path,
    not_on_form=frozenset({"StaticToxicAreaLocation"}),
    sections=(("Static zones", (F("IsToxicZoneActive", "bool", "Off: no static zone is active."),)),),
)

STATIC_ZONES = Table(
    id="toxiczone-static-zones",
    title="ToxicZone Static Zones",
    mod=MOD,
    path=_static_path,
    section="StaticToxicAreaLocation",
    name_key="ToxicZoneStatut",
    add_row=False,
    tree=frozenset({"ParticlesPosition", "ToxicCratePosition", "CreatureList", TREE_REST}),
    warnings=(
        "New zones are made by duplicating one; the copy starts where its original is. "
        "Open a zone to edit its smoke particles, crate positions and creatures.",
    ),
    columns=(
        F("ToxicZoneStatut", "str", "The zone's name.", label="Name"),
        F("X", "float", "Centre, metres east.", unit="meters"),
        F("Y", "float", "Centre, metres north (the map's Z).", unit="meters"),
        F("Radius", "float", "Metres. Not used by a square zone.", unit="meters"),
        F("IsSquareZone", "bool", "On: the zone is the rectangle from X1/Y1 to X2/Y2 instead of a circle."),
        F("X1", "float", "First corner of a square zone; -1 while unused."),
        F("Y1", "float", "First corner of a square zone; -1 while unused."),
        F("X2", "float", "Second corner of a square zone; -1 while unused."),
        F("Y2", "float", "Second corner of a square zone; -1 while unused."),
        *_MESSAGES,
        F("IsOnlyGasMask", "bool"),
        F("IsLootActive", "bool", "Off: no crates spawn in this zone."),
        F("ContainerName", "str", "The container class the loot spawns in."),
        F("NumbCratePerTime", "int", "Crates spawned each loot interval.", label="Crates"),
        F("MaxLoot", "int", "The most items in one crate."),
    ),
)


# ── Moving zones ────────────────────────────────────────────────────────

MOVING_FORM = Form(
    mod=MOD,
    path=_moving_path,
    not_on_form=frozenset({"MovingToxicAreaLocation", "StaticToxicAreaLocation"}),
    sections=(
        ("Moving zones", (
            F("IsMovingZoneActive", "bool", "Off: no moving zone is active."),
            F("AdminUID", "list",
              "Steam64 IDs that get each moving zone's position in chat every minute. A "
              "fresh install lists only the mod author's own ID."),
        )),
    ),
)


def _speed_unsigned(entry: dict) -> dict:
    speed = json_number(entry.get("Speed"))
    if speed is None:
        return entry
    return {**entry, "Speed": int(abs(speed)) if speed == int(speed) else abs(speed)}


MOVING_ZONES = Table(
    id="toxiczone-moving-zones",
    title="ToxicZone Moving Zones",
    mod=MOD,
    path=_moving_path,
    section="MovingToxicAreaLocation",
    name_key="MovingToxicZoneStatut",
    add_row=False,
    view=_speed_unsigned,
    finish=ops.apply_derived,
    # Whatever nested parts a moving zone carries (none in the mod's default).
    tree=frozenset({TREE_REST}),
    warnings=(
        "Each save works out the zone's path (Coeff, YtoOrigin and Norme) from its start and "
        "end, and which way it moves, so only the two points need changing. The path has to "
        "go east or west: start and end cannot share an X.",
    ),
    columns=(
        F("MovingToxicZoneStatut", "str", "The zone's name.", label="Name"),
        F("StartX", "float", "Where the zone starts, metres east.", unit="meters"),
        F("StartY", "float", "Where the zone starts, metres north (the map's Z).", unit="meters"),
        F("EndX", "float", "Where it ends, metres east. After reaching it the zone starts again.", unit="meters"),
        F("EndY", "float", "Where it ends, metres north.", unit="meters"),
        F("Radius", "float", "Metres.", unit="meters"),
        F("Speed", "int",
          "Metres the zone moves east or west each minute. The direction comes from the two "
          "points; on a steep path it covers more ground than this."),
        F("Chance", "float", "Chance, 0 to 1, that the zone appears after a restart.", share=True),
        F("NbSickGiven", "int",
          "The most toxicity an unprotected player takes on per minute in this zone."),
        F("IsOnlyGasMask", "bool"),
        F("ParticleName", "str", "The smoke effect the zone shows."),
        *_MESSAGES,
    ),
)


# ── D4: the author's AdminUID ───────────────────────────────────────────


def replace_author_uid(config: DayzConfig, steam_id: str) -> list[str]:
    """Put *steam_id* where ``AdminUID`` lists the mod author's ID, and return
    the new list. The admin confirms this in the UI; nothing calls it alone."""
    steam_id = (steam_id or "").strip()
    if not is_valid_steam64(steam_id):
        raise ValueError(f"{steam_id!r} is not a Steam64 ID (17 digits starting 7656119)")
    path = _moving_path(config)
    doc = document(path, MOD)
    uids = doc.get("AdminUID")
    if not isinstance(uids, list) or ops.AUTHOR_UID not in uids:
        raise ValueError("AdminUID does not list the mod author's ID")
    new: list[str] = []
    for uid in uids:
        uid = steam_id if uid == ops.AUTHOR_UID else uid
        if uid not in new:
            new.append(uid)
    doc["AdminUID"] = new
    save_json_patched(path, doc)
    return new


def recompute_paths(config: DayzConfig) -> list[str]:
    """Recompute every moving zone's path from its two ends, and return the
    names of the zones that changed. A north-south zone is left as it is; its
    own finding says what to do. Writes only when something changed."""
    path = _moving_path(config)
    doc = document(path, MOD)
    zones = doc.get("MovingToxicAreaLocation")
    changed: list[str] = []
    for i, zone in enumerate(zones if isinstance(zones, list) else []):
        if not isinstance(zone, dict):
            continue
        fixed = dict(zone)
        try:
            ops.apply_derived(fixed)
        except ValueError:
            continue
        if fixed != zone:
            zones[i] = fixed
            changed.append(str(zone.get("MovingToxicZoneStatut") or f"#{i + 1}"))
    if changed:
        save_json_patched(path, doc)
    return changed


# ── Raw tab ─────────────────────────────────────────────────────────────

_validate = whole_number_validator(
    lambda doc: decimals_in_whole_numbers(doc, keys=ops.TOXICZONE.whole_numbers)
)


def _targets(path_fn):
    def targets(config: DayzConfig):
        found = raw_targets_from((path_fn, "json"))(config)
        for t in found:
            t.validate = _validate
        return found

    return targets


# ── Registration ────────────────────────────────────────────────────────


def _register_form(id: str, title: str, form: Form) -> None:
    register(
        EditorSpec(
            id=id,
            title=title,
            group=GROUP,
            kind="form",
            exists=lambda config: form.path(config).is_file(),
            load=lambda config: form_load(config, form),
            save=lambda config, values: form_save(config, form, values),
            raw_targets=_targets(form.path),
        )
    )


def _register_table(table: Table) -> None:
    register(
        EditorSpec(
            id=table.id,
            title=table.title,
            group=GROUP,
            kind="table",
            exists=lambda config: table.path(config).is_file(),
            load=lambda config: table_load(config, table),
            save=lambda config, rows: table_save(config, table, rows),
            raw_targets=_targets(table.path),
        )
    )


_register_form("toxiczone-settings", "ToxicZone Settings", GENERAL_FORM)
_register_form("toxiczone-static", "ToxicZone Static Settings", STATIC_FORM)
_register_table(STATIC_ZONES)
_register_form("toxiczone-moving", "ToxicZone Moving Settings", MOVING_FORM)
_register_table(MOVING_ZONES)
_register_form("toxiczone-loot", "ToxicZone Loot", LOOT_FORM)
_register_form("toxiczone-protection", "ToxicZone Protection", PROTECTION_FORM)
_register_table(CLOTHES)
