"""RadZone editors (plans/48 B): its three RZConfig files as forms and tables.

Built on ``_mod_json.py``, so a save patches and whole numbers stay whole. An
area's crate positions and creatures, a zone's nested parts and a slot's
clothes are edited as a tree under the row (``Table.tree``, plans/76 step 2b). The field help is written here in zsm's own words from
what the mod's explanation files say; nothing of the mod's is copied, and
nothing is ever written inside ``@RadZone/``.

Where a key has no explanation (``NaturalRadDecayPerMin``,
``UseChatMessagesInsteadOfNotifications`` and the crate-area keys), it has no
help text rather than a guess.

The loot lists (``ListLoots``: presets with nested attachment chances) are
deep enough that a table would be a JSON cell per preset, so the crate form
shows them as a tree under its fields (plans/76 step 2, ``Form.tree``), where
a decimal in a whole number is refused as on the crate file's Raw tab.
"""

from __future__ import annotations

from pathlib import Path

from app.ops import zone_mods as ops
from app.shared.config import DayzConfig
from app.shared.json_utils import load_json
from app.web.editors import EditorSpec, register
from app.web.editors._mod_json import (
    TREE_REST,
    F,
    Form,
    Table,
    form_load,
    form_save,
    table_load,
    table_save,
    whole_number_validator,
)
from app.web.editors._raw import raw_targets_from

GROUP = "RadZone"
MOD = "RadZone"


def _radiation_path(config: DayzConfig) -> Path:
    return ops.config_dir(config) / ops.RADIATION


def _crates_path(config: DayzConfig) -> Path:
    return ops.config_dir(config) / ops.CRATES


def _clothes_path(config: DayzConfig) -> Path:
    return ops.config_dir(config) / ops.CLOTHES


# ── Radiation: the settings form ────────────────────────────────────────

RADIATION_FORM = Form(
    mod=MOD,
    path=_radiation_path,
    not_on_form=frozenset({"RadiationAreaLocation"}),
    sections=(
        ("Zones", (
            F("IsRadiationZoneActive", "bool", "Off: no zone makes anyone sick."),
            F("UseChatMessagesInsteadOfNotifications", "bool"),
        )),
        ("Sickness", (
            F("RadChanceVomit", "float", "Chance, 0 to 1, that a sick player vomits.", share=True),
            F("RadChanceBleeding", "float", "Chance, 0 to 1, that a sick player starts bleeding.", share=True),
            F("RadBloodLossDamage", "float", "Blood a sick player loses per minute."),
            F("RadHealthDamage", "float", "Health a sick player loses per minute."),
            F("RadHighMultiplier", "float",
              "Multiplies the blood and health loss in the last stage of the sickness, above "
              "500 radiation."),
            F("QtyRadReducedPerMin", "float",
              "Radiation the cure removes per minute. A player carries at most 600."),
            F("NaturalRadDecayPerMin", "float"),
        )),
        ("Protection", (
            F("RequiredProtection", "int",
              "How many protective pieces a player needs. The default asks for the full set of "
              "six: gas mask, hood, jacket, gloves, trousers and boots."),
            F("CriticalProtection", "int",
              "Outside radiation above this makes a player sick and wears their suit down, "
              "protected or not."),
            F("SuitsDamage", "int",
              "Percent damage a suit takes per minute once the outside radiation is above "
              "CriticalProtection."),
            F("ClothesQuantityMultiplier", "float",
              "Share of the outside radiation that worn clothes take on each minute. It keeps "
              "the highest value reached rather than adding up."),
            F("AmountGivenAfterCriticalProtection", "float",
              "Share of the outside radiation a player takes on once it is above "
              "CriticalProtection."),
        )),
    ),
)


# ── Radiation: the zones ────────────────────────────────────────────────

ZONES = Table(
    id="radzone-zones",
    title="RadZone Zones",
    mod=MOD,
    path=_radiation_path,
    section="RadiationAreaLocation",
    name_key="RadiationZoneStatut",
    # A zone is a position; a blank one would sit at the map's corner. New
    # zones are copies, the same rule Dynamic AI's positioned entries follow.
    add_row=False,
    # Whatever nested parts a zone carries (none in the mod's default).
    tree=frozenset({TREE_REST}),
    warnings=(
        "New zones are made by duplicating one; the copy starts where its original is. "
        "Change X and Y (or the corners) to move it.",
    ),
    columns=(
        F("RadiationZoneStatut", "str",
          "The zone's name. The crate areas in CrateSystemConfigV1.json name their zone "
          "with it.", label="Name"),
        F("X", "float", "Centre, metres east.", unit="meters"),
        F("Y", "float", "Centre, metres north (the map's Z).", unit="meters"),
        F("Radius", "float", "Metres. Not used by a square zone.", unit="meters"),
        F("IsSquareZone", "bool", "On: the zone is the rectangle from X1/Y1 to X2/Y2 instead of a circle."),
        F("X1", "float", "Bottom-left corner of a square zone; -1 while unused."),
        F("Y1", "float", "Bottom-left corner of a square zone; -1 while unused."),
        F("X2", "float", "Top-right corner of a square zone; -1 while unused."),
        F("Y2", "float", "Top-right corner of a square zone; -1 while unused."),
        F("NbSickGivenForRadiation", "int",
          "Radiation at the centre. It is lower towards the edge.", label="Radiation"),
        F("IsMsgActive", "bool", "Tell players when they enter and leave the zone."),
        F("MsgEnterZone", "str"),
        F("MsgExitZone", "str"),
    ),
)


# ── Crates ──────────────────────────────────────────────────────────────

CRATES_FORM = Form(
    mod=MOD,
    path=_crates_path,
    not_on_form=frozenset({"ListLoots", "AreaLocation"}),
    tree=frozenset({"ListLoots"}),
    sections=(
        ("Crates", (
            F("IsCrateSystemActive", "bool", "Off: no loot crates spawn in the zones."),
            F("TimerLoot", "int",
              "Milliseconds before a crate disappears and a new one spawns. 2700000 is 45 minutes."),
        )),
    ),
)

CRATE_AREAS = Table(
    id="radzone-crate-areas",
    title="RadZone Crate Areas",
    mod=MOD,
    path=_crates_path,
    section="AreaLocation",
    name_key="RZName",
    add_row=False,
    tree=frozenset({"RZCrateLocations", "CreaturesLists", TREE_REST}),
    warnings=(
        "Open an area to edit its crate positions and creatures. New areas are made by "
        "duplicating one.",
    ),
    columns=(
        F("RZName", "str", "The zone this area belongs to, by its name.", "zones", label="Zone"),
        F("RZNbCrate", "int", label="Crates"),
    ),
)


# ── Clothes ─────────────────────────────────────────────────────────────

CLOTHES = Table(
    id="radzone-clothes",
    title="RadZone Clothes Protection",
    mod=MOD,
    path=_clothes_path,
    section="RZListSlotProtection",
    name_key="SlotName",
    add_row=False,
    tree=frozenset({"ListProtections"}),
    warnings=(
        "Open a slot to edit its clothes and how much each protects. RadZone checks them in "
        "order, so keep a vanilla item after the mod's own when both are listed.",
    ),
    columns=(F("SlotName", "str", "The clothing slot."),),
)


def _zone_choices(config: DayzConfig) -> dict[str, list[str]]:
    path = _radiation_path(config)
    if not path.is_file():
        return {"zones": []}
    try:
        doc = load_json(path)
    except (OSError, ValueError):
        return {"zones": []}
    return {"zones": sorted({z.name for z in ops.read_zones(doc) if z.name})}


# ── Raw tab ─────────────────────────────────────────────────────────────

_validate = whole_number_validator(ops.decimals_in_whole_numbers)


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
            load=lambda config: table_load(config, table, _zone_choices(config)),
            save=lambda config, rows: table_save(config, table, rows),
            raw_targets=_targets(table.path),
        )
    )


_register_form("radzone-settings", "RadZone Settings", RADIATION_FORM)
_register_table(ZONES)
_register_form("radzone-crates", "RadZone Crates", CRATES_FORM)
_register_table(CRATE_AREAS)
_register_table(CLOTHES)
