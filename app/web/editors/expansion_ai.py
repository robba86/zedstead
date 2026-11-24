"""Expansion AI's patrols as a table (plans/64 D1).

``AIPatrolSettings.json`` is one of Expansion's 30 settings files, and its
form (``expansion.py``) shows only the top-level values: ``Patrols`` is a list
of objects, which a flat form cannot show. This table is one row per patrol,
over the same file, with the shared machinery in ``_mod_json`` — so a save
re-reads the file and writes back only the columns below. Waypoints, units,
``ObjectClassName`` and every key not listed here stay exactly as they were.

Positions are not columns: the Visual Map Editor owns them. ``ObjectClassName``
is not one either, because changing it changes what the waypoints mean (an
object patrol's waypoints are relative to its building, plans/64 B4).

The checks are ``app/ops/expansion_ai.py``; the pickers come from the install
(``ai_refs.install_choices``) and from the file's own load-balancing table.
"""

from __future__ import annotations

from pathlib import Path

from app.ops import ai_refs
from app.ops import expansion_ai as ops
from app.shared.config import DayzConfig
from app.shared.json_utils import load_json
from app.web.editors import EditorSpec, register
from app.web.editors._mod_json import F, Table, readable, table_load, table_save
from app.web.editors._raw import raw_targets_from
from app.web.editors.expansion import GROUP

_JSON = ".json"


def _sorted(values) -> tuple[str, ...]:
    return tuple(sorted(values))


PATROLS = Table(
    id="expansion-ai-patrol-list",
    title="Expansion AI Patrol List",
    mod=ops.MOD_NAME,
    path=ops.patrol_path,
    section="Patrols",
    add_row=False,
    name_key="Name",
    warnings=(
        "Positions are not in this table: the Map Editor places waypoints.",
        "An object patrol (one with an ObjectClassName) runs at every building of "
        "that class, and its waypoints are metres from the building, not map positions.",
    ),
    columns=(
        F("Name", "str", "Names the patrol in Expansion's log. A persistent patrol needs "
          "one that no other persistent patrol uses."),
        F("Faction", "str", "RANDOM lets Expansion pick a registered faction. An unknown "
          "name spawns the patrol as Civilian.", "factions"),
        F("Loadout", "str", "A file in ExpansionMod/Loadouts; .json may be left off. "
          "Empty: the faction's default loadout.", "loadouts"),
        F("LoadBalancingCategory", "str", "Empty: ObjectPatrol for an object patrol, "
          "Patrol otherwise. A name the file's table does not have counts under Global.",
          "categories", label="Category"),
        F("NumberOfAI", "int", "0 sets up no patrol. Below 0: a random count from 1 to "
          "the number without its sign.", label="AI"),
        F("NumberOfAIMax", "int", "Above 0: a random count from NumberOfAI up to this.",
          label="AI max"),
        F("Chance", "float", "0 never spawns; 1 always does.", share=True),
        F("Behaviour", "str", "", _sorted(ops.BEHAVIOURS)),
        F("Speed", "str", "", _sorted(ops.SPEEDS)),
        F("UnderThreatSpeed", "str", "", _sorted(ops.SPEEDS), label="Threat speed"),
        F("AccuracyMin", "float", "0 or below: the file's value, then AISettings.json's."),
        F("AccuracyMax", "float", "0 or below: the file's value, then AISettings.json's."),
        F("MinDistRadius", "float", "Below 0: the file's value. Expansion swaps the two "
          "radii when the minimum ends up above the maximum.", label="Min dist", unit="meters"),
        F("MaxDistRadius", "float", "0 or below: the file's value.", label="Max dist", unit="meters"),
        F("RespawnTime", "float", "Seconds. -1 never respawns; -2 uses the file's value.", unit="seconds"),
        F("Persist", "bool", "Keep the patrol across restarts. Not for object patrols."),
        F("CanBeLooted", "bool"),
        F("LootDropOnDeath", "str", "A file in ExpansionMod/AI/LootDrops; .json may be "
          "left off. Empty: none.", "lootdrops", label="Loot drop"),
    ),
)


def _install_choices(config: DayzConfig) -> dict[str, list[str]]:
    choices = ai_refs.install_choices(config, loadout_stems=True, random=True)
    drops = ops.loot_drops_dir(config)
    stems = sorted(p.name[: -len(_JSON)] for p in drops.glob("*" + _JSON)) if drops.is_dir() else []
    categories: list[str] = []
    path = ops.patrol_path(config)
    if readable(path):
        try:
            doc = load_json(path)
        except (OSError, ValueError):
            doc = None
        table = doc.get("LoadBalancingCategories") if isinstance(doc, dict) else None
        if isinstance(table, dict):
            categories = sorted(table)
    return {
        "factions": choices["factions"],
        # Empty is a value in all three: the faction's default loadout, the
        # patrol kind's category, no loot drop.
        "loadouts": ["", *choices["loadouts"]],
        "categories": ["", *categories],
        "lootdrops": ["", *stems],
    }


def _path(config: DayzConfig) -> Path | None:
    return ops.patrol_path(config)


register(
    EditorSpec(
        id=PATROLS.id,
        title=PATROLS.title,
        group=GROUP,
        kind="table",
        exists=lambda config: readable(_path(config)),
        load=lambda config: table_load(config, PATROLS, _install_choices(config)),
        save=lambda config, rows: table_save(config, PATROLS, rows),
        raw_targets=raw_targets_from((_path, "json")),
    )
)
