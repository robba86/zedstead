"""The mods zsm has editors for: the Mods page's Install -> Supported list.

One entry per Configs -> Mods subtab (an ``EditorSpec.group``), so the list
follows the editors rather than a second taxonomy. Each names the mod that
makes the group work, the map editor layers it unlocks, and a sentence on what
zsm does for it. Whether it is installed is matched by workshop ID, like every
other pre-configured mod's check (``mod_files.installed_mod_name``).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modmgr import expansion
from app.modmgr.mod_ops import TRADERPLUS_WORKSHOP_ID
from app.ops import dynamic_ai, toxic_zone, zone_mods
from app.ops.mod_files import installed_mod_name


@dataclass(frozen=True)
class SupportedMod:
    group: str  # the EditorSpec.group, which is also the Configs subtab
    name: str  # the mod's name in the mod list
    workshop_id: str
    blurb: str
    #: Map editor layer keys (``LayerToggles.jsx``) this mod's files draw.
    layers: tuple[str, ...] = ()
    #: True when it is installed from the Expansion picker, not by its id.
    picker: bool = False


SUPPORTED_MODS: tuple[SupportedMod, ...] = (
    SupportedMod(
        group="TraderPlus",
        name="@TraderPlus",
        workshop_id=TRADERPLUS_WORKSHOP_ID,
        blurb="Traders, prices, banking, garage, insurance and safe zones. "
        "Its default configs are copied in on install.",
        layers=("traders",),
    ),
    SupportedMod(
        group="Expansion",
        name=expansion.module_name(expansion.CORE_ID),
        workshop_id=expansion.CORE_ID,
        blurb="Every Expansion settings file, AI patrols and roaming, the Market, "
        "spawn points, safe zones and the mission templates. Pick the modules you want.",
        layers=(
            "exptraderzones", "expsafezones", "expspawns", "expaipatrols", "expairoaming",
            "expmarket", "expmarkers", "expbuildzones", "expmissions",
        ),
        picker=True,
    ),
    SupportedMod(
        group="Dynamic AI",
        name=dynamic_ai.MOD_NAME,
        workshop_id=dynamic_ai.WORKSHOP_ID,
        blurb="AI points, locations and audio locations, with checks on its settings "
        "after each boot. Needs Expansion AI, which is installed with it.",
        layers=("dynaipoints", "dynailocations", "dynaiaudio"),
    ),
    SupportedMod(
        group="RadZone",
        name=zone_mods.MOD_NAME,
        workshop_id=zone_mods.WORKSHOP_ID,
        blurb="Radiation zones, loot crate areas and clothes protection, with checks "
        "for zones left on the Chernarus defaults.",
        layers=("radzone", "radzonecrates"),
    ),
    SupportedMod(
        group="ToxicZone",
        name=toxic_zone.MOD_NAME,
        workshop_id=toxic_zone.TOXICZONE.workshop_id,
        blurb="Static and moving toxic zones, loot crates and clothes protection.",
        layers=("toxiczone", "toxicmoving", "toxiccrates"),
    ),
)


def supported_mods(config, editor_counts: dict[str, int]) -> list[dict]:
    """The list as the page shows it. *editor_counts* is editors per group.
    A picker mod also lists its installed modules, which FileMissing.jsx names
    when a module's settings file is missing."""
    from app.ops.expansion_ops import installed_module_labels

    return [
        {
            "group": m.group,
            "name": m.name,
            "id": m.workshop_id,
            "blurb": m.blurb,
            "layers": list(m.layers),
            "picker": m.picker,
            "editors": editor_counts.get(m.group, 0),
            "installed": installed_mod_name(config, m.name, m.workshop_id) is not None,
            **({"modules": installed_module_labels(config)} if m.picker else {}),
        }
        for m in SUPPORTED_MODS
    ]
